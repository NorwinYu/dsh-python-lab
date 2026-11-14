import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .cancellation import CancellationToken
from .llm import text_block
from .plugins import Plugin, ServiceContext
from .schedule_time import TimeRuleError, next_occurrence, parse_utc, utc_text, zone
from .session import SessionStore
from .subagents import SubagentService
from .system_prompt import SystemPrompt
from .tools import Execution, Tool, ToolError, ToolRegistry


UTC = timezone.utc
SCHEDULE_GUIDANCE = "Only create or modify scheduled tasks at the user's explicit request; prior explicit user authorization for the running scheduled task remains valid."
TARGET_CHOICES = ["this_session", "new_once", "new"]


class ScheduleError(ToolError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def now() -> datetime:
    return datetime.now(UTC)


def slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "task"
    return f"{base}-{uuid.uuid4().hex[:12]}"


def local_text(record: Dict[str, object], value: str) -> str:
    if record["kind"] == "rrule":
        first = str(record["rrule"]).splitlines()[0]
        match = re.match(r"DTSTART;TZID=([^:]+):", first)
        zone_name = match.group(1) if match else "UTC"
    elif record["kind"] in {"daily", "weekly", "cron"}:
        zone_name = str(record["timeZone"])
    else:
        zone_name = "UTC"
    return parse_utc(value).astimezone(zone(zone_name)).isoformat(timespec="milliseconds")


class ScheduleService:
    def __init__(self, ctx: ServiceContext, session: SessionStore, path: Path, new_interval: int = 60) -> None:
        if not 60 <= new_interval <= 604800:
            raise ValueError("MINIDSH_SCHEDULE_NEW_INTERVAL_SECONDS must be from 60 through 604800")
        self.ctx = ctx
        self.session = session
        self.path = path
        self.new_interval = new_interval
        self._tasks: Dict[str, Dict[str, object]] = {}
        self._lock = threading.RLock()
        self._active: Dict[str, int] = {}
        self._pending: Dict[str, List[Dict[str, object]]] = {}
        self._inflight: Dict[str, Dict[str, object]] = {}
        self._closed = threading.Event()
        self._wake = threading.Condition(self._lock)
        self._load()
        ctx.events.on("turn/start", self._turn_start)
        ctx.events.on("turn/end", self._turn_end)
        ctx.events.on("schedule/inbox", self._inbox)
        self._thread = threading.Thread(target=self._timer, daemon=True)
        self._thread.start()

    @property
    def agent(self) -> object:
        return self.ctx.require("agent")

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        unit = data.get("unit", {})
        if unit.get("name") != "schedule" or unit.get("version") not in {1, 2}:
            raise ValueError("unsupported schedule storage")
        tasks = data.get("tables", {}).get("tasks", {})
        if not isinstance(tasks, dict):
            raise ValueError("invalid schedule task table")
        for task_id, task in tasks.items():
            if not isinstance(task, dict) or not isinstance(task.get("record"), dict):
                raise ValueError(f"invalid schedule task: {task_id}")
            record = task["record"]
            if record.get("id") != task_id or record.get("kind") not in {"after", "at", "every", "daily", "weekly", "cron", "rrule"}:
                raise ValueError(f"invalid schedule record: {task_id}")
            parse_utc(str(record["scheduledAt"]))
            task.setdefault("status", "active")
            task.setdefault("involvedSessionIds", [])
            task.setdefault("deliveryHistory", {"records": [], "earlierRecordsUnavailable": False, "earlierRecordsPruned": False})
            task.setdefault("occurrenceCount", 0)
            self._tasks[task_id] = task

    def _save(self) -> None:
        with self._wake:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"unit": {"name": "schedule", "version": 2}, "global": None, "tables": {"tasks": self._tasks}}
            temporary = self.path.parent / f".{self.path.name}.{uuid.uuid4().hex}.tmp"
            try:
                temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temporary, self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            self.ctx.events.emit("schedule/changed", {})
            self._wake.notify_all()

    @staticmethod
    def _title_prompt(title: object, prompt: object) -> Tuple[str, str]:
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
            raise ScheduleError("invalid_prompt", "title must be non-empty and at most 120 characters")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ScheduleError("invalid_prompt", "prompt must be non-empty")
        return title.strip(), prompt.strip()

    def _target(self, target: object, actor: Optional[str]) -> Dict[str, object]:
        if target is None:
            return {"kind": "new_once", "sessionId": uuid.uuid4().hex, "create": {}}
        if not isinstance(target, dict) or target.get("kind") not in {"session", "new_once", "new"}:
            raise ScheduleError("invalid_selector", "invalid schedule target")
        result = dict(target)
        if result["kind"] == "session":
            session_id = result.get("sessionId")
            if not isinstance(session_id, str) or not self.session.load(session_id):
                raise ScheduleError("invalid_selector", "target session does not exist")
            subagents = self.ctx.optional("subagents")
            if isinstance(subagents, SubagentService):
                try:
                    if subagents.record(session_id)["depth"] > 0:
                        raise ScheduleError("subagent_session", "A delegated subagent cannot receive scheduled tasks.")
                except Exception as exc:
                    if isinstance(exc, ScheduleError):
                        raise
        elif result["kind"] == "new_once":
            result["sessionId"] = uuid.uuid4().hex
            result.setdefault("create", {})
        else:
            result["create"] = result.get("create", {})
        return result

    def create(self, title: object, prompt: object, timing: Dict[str, object], target: object = None, actor: Optional[str] = None) -> Dict[str, object]:
        title, prompt = self._title_prompt(title, prompt)
        if not isinstance(timing, dict) or not isinstance(timing.get("kind"), str):
            raise ScheduleError("invalid_rule", "timing.kind is required")
        try:
            created = now()
            record = self._record(timing, created)
        except TimeRuleError as exc:
            raise ScheduleError(exc.code, str(exc)) from exc
        task_id = slug(title)
        record["id"] = task_id
        record["title"] = title
        record["prompt"] = prompt
        with self._wake:
            task = {
                "record": record,
                "target": self._target(target, actor),
                "status": "active",
                "involvedSessionIds": [actor] if actor is not None else [],
                "createdAt": utc_text(created),
                "updatedAt": utc_text(created),
                "deliveryHistory": {"records": [], "earlierRecordsUnavailable": False, "earlierRecordsPruned": False},
                "occurrenceCount": 0,
            }
            self._tasks[task_id] = task
            self._save()
            return self.public(task)

    def _record(self, timing: Dict[str, object], created: datetime) -> Dict[str, object]:
        kind = timing["kind"]
        if kind == "rrule":
            text = timing.get("rrule")
            first = next_occurrence({"kind": "rrule", "rrule": text}, created)
            if first is None:
                raise TimeRuleError("rrule has no future occurrence", "not_future")
            return {"kind": "rrule", "rrule": text, "scheduledAt": utc_text(first)}
        if kind == "after":
            seconds = timing.get("after_seconds", timing.get("afterSeconds"))
            if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 1:
                raise TimeRuleError("after_seconds must be positive")
            return {"kind": "after", "afterSeconds": seconds, "scheduledAt": utc_text(created + timedelta(seconds=seconds))}
        if kind == "at":
            target = timing.get("at", timing.get("scheduledAt"))
            parsed = parse_utc(target) if isinstance(target, str) else None
            if parsed is None or parsed <= created:
                raise TimeRuleError("at must be in the future", "not_future")
            return {"kind": "at", "scheduledAt": utc_text(parsed)}
        if kind == "every":
            seconds = timing.get("every_seconds", timing.get("everySeconds"))
            if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 60:
                raise TimeRuleError("every_seconds must be at least 60")
            return {"kind": "every", "everySeconds": seconds, "scheduledAt": utc_text(created + timedelta(seconds=seconds))}
        if kind in {"daily", "weekly", "cron"}:
            selector = timing.get(kind, timing)
            if not isinstance(selector, dict):
                raise TimeRuleError(f"{kind} selector must be an object")
            record: Dict[str, object] = {"kind": kind, "timeZone": selector.get("time_zone", selector.get("timeZone"))}
            if kind == "cron":
                record["expression"] = selector.get("expression")
            else:
                record["time"] = selector.get("time")
                if kind == "weekly":
                    record["weekdays"] = selector.get("weekdays")
            first = next_occurrence(record, created)
            if first is None:
                raise TimeRuleError("rule has no future occurrence", "not_future")
            record["scheduledAt"] = utc_text(first)
            return record
        raise TimeRuleError(f"unsupported schedule kind: {kind}")

    def public(self, task: Dict[str, object]) -> Dict[str, object]:
        record = dict(task["record"])
        result = {
            **record,
            "scheduledAtLocal": local_text(record, str(record["scheduledAt"])),
            "target": dict(task["target"]),
            "status": task["status"],
        }
        if task.get("updatedAt") is not None:
            result["updatedAt"] = task["updatedAt"]
        history = task["deliveryHistory"]["records"]
        if history:
            latest = history[-1]
            result["lastDelivery"] = {key: latest[key] for key in ("scheduledAt", "deliveredAt", "targetSessionId")}
        return result

    def list(self, active: Optional[bool] = None, limit: int = 100, offset: int = 0) -> Dict[str, object]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500 or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ScheduleError("invalid_rule", "limit must be 1..500 and offset non-negative")
        if active is not None and not isinstance(active, bool):
            raise ScheduleError("invalid_rule", "active must be boolean")
        with self._lock:
            rows = [task for task in self._tasks.values() if active is None or (task["status"] == "active") == active]
            active_rows = sorted((task for task in rows if task["status"] == "active"), key=lambda task: task["record"]["scheduledAt"])
            inactive_rows = sorted((task for task in rows if task["status"] != "active"), key=lambda task: task.get("updatedAt", ""), reverse=True)
            rows = active_rows + inactive_rows
            return {"items": [self.public(task) for task in rows[offset:offset + limit]], "shown": max(0, min(limit, len(rows) - offset)), "total": len(rows), "limit": limit, "offset": offset}

    def view(self, task_id: str) -> Dict[str, object]:
        with self._lock:
            task = self._tasks.get(task_id)
            return self.public(task) if task is not None else {"id": task_id, "code": "schedule_not_found"}

    def edit(self, task_id: str, action: str, patch: Dict[str, object], expected: Optional[str] = None, actor: Optional[str] = None) -> Dict[str, object]:
        with self._wake:
            task = self._tasks.get(task_id)
            if task is None:
                return {"id": task_id, "code": "schedule_not_found"}
            if expected is not None and expected != task.get("updatedAt"):
                return {"id": task_id, "code": "schedule_conflict"}
            if action == "delete":
                if patch:
                    raise ScheduleError("invalid_selector", "delete accepts no patch fields")
                del self._tasks[task_id]
                self._save()
                return {"id": task_id, "deleted": True}
            if action != "update" or not patch:
                raise ScheduleError("invalid_selector", "update requires at least one field")
            if set(patch) - {"title", "prompt", "timing", "target", "active"}:
                raise ScheduleError("invalid_selector", "unknown update field")
            updated = dict(task)
            updated["record"] = dict(task["record"])
            updated["deliveryHistory"] = task["deliveryHistory"]
            if "title" in patch or "prompt" in patch:
                title, prompt = self._title_prompt(patch.get("title", task["record"]["title"]), patch.get("prompt", task["record"]["prompt"]))
                updated["record"]["title"] = title
                updated["record"]["prompt"] = prompt
            if "target" in patch:
                updated["target"] = self._target(patch["target"], actor)
            if "timing" in patch:
                try:
                    replacement = self._record(patch["timing"], now())
                except TimeRuleError as exc:
                    raise ScheduleError(exc.code, str(exc)) from exc
                replacement.update({"id": task_id, "title": updated["record"]["title"], "prompt": updated["record"]["prompt"]})
                updated["record"] = replacement
                updated["occurrenceCount"] = 0
            if patch.get("active") is False:
                updated["status"] = "paused"
            elif patch.get("active") is True:
                try:
                    following = next_occurrence(updated["record"], now(), int(updated.get("occurrenceCount", 0)))
                except TimeRuleError as exc:
                    raise ScheduleError(exc.code, str(exc)) from exc
                if following is None:
                    raise ScheduleError("not_future", "task has no future occurrence")
                updated["record"]["scheduledAt"] = utc_text(following)
                updated["status"] = "active"
            previous = parse_utc(str(task.get("updatedAt", task["createdAt"])))
            updated["updatedAt"] = utc_text(max(now(), previous + timedelta(milliseconds=1)))
            if actor is not None and actor not in updated["involvedSessionIds"]:
                updated["involvedSessionIds"] = list(updated["involvedSessionIds"]) + [actor]
            self._tasks[task_id] = updated
            self._save()
            return self.public(updated)

    def history(self, task_id: str, limit: int = 100, before: Optional[str] = None) -> Dict[str, object]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ScheduleError("invalid_rule", "history limit must be 1..100")
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ScheduleError("schedule_not_found", "schedule task not found")
            records = list(reversed(task["deliveryHistory"]["records"]))
            if before is not None:
                ids = [record["messageId"] for record in records]
                if before not in ids:
                    raise ScheduleError("invalid_rule", "unknown history cursor")
                records = records[ids.index(before) + 1:]
            return {"items": records[:limit], "earlierRecordsUnavailable": task["deliveryHistory"].get("earlierRecordsUnavailable", False), "earlierRecordsPruned": task["deliveryHistory"].get("earlierRecordsPruned", False)}

    def delete_inactive(self) -> Dict[str, int]:
        with self._wake:
            ended = [task_id for task_id, task in self._tasks.items() if task["status"] == "ended"]
            for task_id in ended:
                del self._tasks[task_id]
            if ended:
                self._save()
            return {"deleted": len(ended)}

    def delivery_limits(self) -> Dict[str, int]:
        return {"newSessionIntervalSeconds": self.new_interval}

    def run(self, task_id: str) -> Dict[str, object]:
        with self._wake:
            task = self._tasks.get(task_id)
            if task is None:
                return {"id": task_id, "code": "schedule_not_found"}
            latest = task["deliveryHistory"]["records"][-1] if task["deliveryHistory"]["records"] else None
            if task_id in self._inflight or latest is not None and now() - parse_utc(latest["deliveredAt"]) < timedelta(seconds=60):
                return {**self.public(task), "code": "schedule_execution_pending"}
            occurrence = now()
            self._deliver(task, utc_text(occurrence), False)
            return self.public(task)

    def take_pending(self, session_id: str) -> List[Dict[str, object]]:
        with self._lock:
            pending = self._pending.pop(session_id, [])
            return [{"text": item["text"], "source": item["source"]} for item in pending]

    def _turn_start(self, payload: Dict[str, object]) -> None:
        session_id = str(payload["session_id"])
        with self._lock:
            self._active[session_id] = self._active.get(session_id, 0) + 1

    def _turn_end(self, payload: Dict[str, object]) -> None:
        session_id = str(payload["session_id"])
        with self._lock:
            count = self._active.get(session_id, 0) - 1
            if count > 0:
                self._active[session_id] = count
            else:
                self._active.pop(session_id, None)

    def _deliver(self, task: Dict[str, object], scheduled_at: str, move: bool) -> None:
        task_id = str(task["record"]["id"])
        target = task["target"]
        target_id = target.get("sessionId")
        message_id = uuid.uuid4().hex
        source = {"kind": "schedule", "scheduleId": task_id, "occurrenceAt": scheduled_at, "messageId": message_id}
        text = "[SCHEDULE REMINDER]\nThis is a scheduled message from the user\n\n" + json.dumps({"schedule_id": task_id, "occurrence_at": scheduled_at, "reminder_prompt": task["record"]["prompt"]}, ensure_ascii=False)
        delivery = {"text": text, "source": source, "move": move, "task_id": task_id}
        self._inflight[task_id] = delivery
        if target["kind"] == "session" and target_id in self._active:
            self._pending.setdefault(str(target_id), []).append(delivery)
            return
        threading.Thread(target=self._run_delivery, args=(delivery, target), daemon=True).start()

    def _run_delivery(self, delivery: Dict[str, object], target: Dict[str, object]) -> None:
        try:
            target_id = target.get("sessionId")
            exists = isinstance(target_id, str) and bool(self.session.load(target_id))
            self.agent.run(
                delivery["text"],
                target_id if exists else None,
                str(delivery["source"]["messageId"]),
                message_source=delivery["source"],
                new_session_id=target_id if isinstance(target_id, str) else None,
            )
        except Exception:
            with self._wake:
                self._inflight.pop(delivery["task_id"], None)

    def _inbox(self, payload: Dict[str, object]) -> None:
        source = payload.get("source", {})
        task_id = str(source.get("scheduleId", ""))
        with self._wake:
            task = self._tasks.get(task_id)
            if task is None:
                self._inflight.pop(task_id, None)
                return
            delivery = self._inflight.get(task_id)
            if delivery is None or delivery["source"]["messageId"] != source.get("messageId"):
                return
            target_id = str(payload["session_id"])
            delivered_at = utc_text(now())
            receipt = {
                "scheduledAt": source["occurrenceAt"],
                "deliveredAt": delivered_at,
                "messageId": source["messageId"],
                "targetSessionId": target_id,
                "prompt": task["record"]["prompt"],
            }
            history = task["deliveryHistory"]
            history["records"].append(receipt)
            if len(history["records"]) > 200:
                history["records"] = history["records"][-200:]
                history["earlierRecordsPruned"] = True
            cutoff = now() - timedelta(days=30)
            history["records"] = [record for record in history["records"] if parse_utc(record["deliveredAt"]) >= cutoff]
            if task["target"]["kind"] == "new_once":
                task["target"] = {"kind": "session", "sessionId": target_id}
            if delivery["move"]:
                task["occurrenceCount"] = int(task.get("occurrenceCount", 0)) + 1
                following = next_occurrence(task["record"], parse_utc(source["occurrenceAt"]), int(task["occurrenceCount"]))
                current = now()
                if following is not None and following <= current:
                    following = next_occurrence(task["record"], current, int(task["occurrenceCount"]))
                if following is None:
                    task["status"] = "ended"
                else:
                    task["record"]["scheduledAt"] = utc_text(following)
                    task["status"] = "active"
            self._inflight.pop(task_id, None)
            self._save()

    def _timer(self) -> None:
        while not self._closed.is_set():
            with self._wake:
                current = now()
                due = [task for task in self._tasks.values() if task["status"] == "active" and task["record"]["id"] not in self._inflight and parse_utc(task["record"]["scheduledAt"]) <= current]
                for task in due:
                    latest = task["deliveryHistory"]["records"][-1] if task["deliveryHistory"]["records"] else None
                    interval = self.new_interval if task["target"]["kind"] == "new" else 60
                    if latest is not None and current - parse_utc(latest["deliveredAt"]) < timedelta(seconds=interval):
                        continue
                    self._deliver(task, str(task["record"]["scheduledAt"]), True)
                self._wake.wait(0.1)

    def close(self) -> None:
        self._closed.set()
        with self._wake:
            self._wake.notify_all()
        self._thread.join(timeout=2)


class SchedulePlugin(Plugin):
    name = "core.schedule"
    provides = ("schedule",)
    requires = ("session",)

    def __init__(self, path: Path) -> None:
        self.path = path
        self.service: Optional[ScheduleService] = None

    def activate(self, ctx: ServiceContext) -> None:
        session = ctx.require("session")
        assert isinstance(session, SessionStore)
        try:
            interval = int(os.environ.get("MINIDSH_SCHEDULE_NEW_INTERVAL_SECONDS", "60"))
        except ValueError as exc:
            raise ValueError("MINIDSH_SCHEDULE_NEW_INTERVAL_SECONDS must be an integer") from exc
        self.service = ScheduleService(ctx, session, self.path, interval)
        ctx.provide("schedule", self.service, self.name)

    def deactivate(self, ctx: ServiceContext) -> None:
        if self.service is not None:
            self.service.close()


class ScheduleToolPlugin(Plugin):
    name = "tools.schedule"
    requires = ("tools", "systemPrompt", "schedule")

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        schedule = ctx.require("schedule")
        assert isinstance(registry, ToolRegistry) and isinstance(prompt, SystemPrompt) and isinstance(schedule, ScheduleService)

        def caller(execution: Execution) -> str:
            if not execution.session_id:
                raise ScheduleError("internal_error", "The schedule operation failed.")
            subagents = ctx.optional("subagents")
            if isinstance(subagents, SubagentService):
                try:
                    if subagents.record(execution.session_id)["depth"] > 0:
                        raise ScheduleError("subagent_session", "A delegated subagent cannot use scheduled tasks.")
                except ScheduleError:
                    raise
                except Exception:
                    pass
            return execution.session_id

        def target_for(value: object, session_id: str) -> object:
            if value == "this_session":
                return {"kind": "session", "sessionId": session_id}
            if value in {"new_once", "new"}:
                return {"kind": value, "create": {}}
            raise ScheduleError("invalid_selector", "target must be this_session, new_once, or new")

        def create(arguments: Dict[str, object], execution: Execution) -> str:
            actor = caller(execution)
            result = schedule.create(arguments.get("title"), arguments.get("prompt"), {"kind": "rrule", "rrule": arguments.get("rrule")}, target_for(arguments.get("target"), actor), actor)
            return json.dumps(result, ensure_ascii=False)

        def list_tasks(arguments: Dict[str, object], execution: Execution) -> str:
            caller(execution)
            result = schedule.list(arguments.get("active"), arguments.get("limit", 100), arguments.get("offset", 0))
            return json.dumps(result, ensure_ascii=False)

        def view(arguments: Dict[str, object], execution: Execution) -> str:
            caller(execution)
            task_id = arguments.get("id")
            if not isinstance(task_id, str):
                raise ScheduleError("invalid_selector", "id must be a string")
            return json.dumps(schedule.view(task_id), ensure_ascii=False)

        def edit(arguments: Dict[str, object], execution: Execution) -> str:
            actor = caller(execution)
            task_id, action = arguments.get("id"), arguments.get("action")
            if not isinstance(task_id, str) or action not in {"update", "delete"}:
                raise ScheduleError("invalid_selector", "id and update/delete action are required")
            patch: Dict[str, object] = {}
            if action == "update":
                for key in ("title", "prompt", "active"):
                    if key in arguments:
                        patch[key] = arguments[key]
                if "rrule" in arguments:
                    patch["timing"] = {"kind": "rrule", "rrule": arguments["rrule"]}
                if "target" in arguments:
                    patch["target"] = target_for(arguments["target"], actor)
            expected = arguments.get("expectedUpdatedAt")
            if expected is not None and not isinstance(expected, str):
                raise ScheduleError("invalid_selector", "expectedUpdatedAt must be a string")
            return json.dumps(schedule.edit(task_id, action, patch, expected, actor), ensure_ascii=False)

        target_schema = {"type": "string", "enum": TARGET_CHOICES}
        tools = [
            Tool("schedule_create", "Create a scheduled task. At each occurrence of rrule, prompt is delivered to the target session as a scheduled message from the user. After downtime, only the latest missed occurrence is delivered. Delivery can repeat after a crash.",
                 {"type": "object", "properties": {"title": {"type": "string", "description": "Task name of at most 120 characters in the user's language."}, "prompt": {"type": "string", "description": "Instruction delivered at each occurrence."}, "rrule": {"type": "string", "description": "One DTSTART line, a newline, and one RRULE line in the supported RFC 5545 subset."}, "target": target_schema}, "required": ["title", "prompt", "rrule", "target"], "additionalProperties": False}, create, kind="schedule"),
            Tool("schedule_list", "List scheduled tasks, including tasks created from other sessions. Active tasks come first, ordered by scheduledAt.",
                 {"type": "object", "properties": {"active": {"type": "boolean"}, "limit": {"type": "integer", "description": "Page size from 1 to 500; defaults to 100."}, "offset": {"type": "integer", "description": "Number of tasks to skip; defaults to 0."}}, "additionalProperties": False}, list_tasks, kind="schedule"),
            Tool("schedule_view", "View one scheduled task by id.", {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False}, view, kind="schedule"),
            Tool("schedule_edit", "Update, pause, resume, or delete one scheduled task by id. Deleting does not retract queued messages.",
                 {"type": "object", "properties": {"id": {"type": "string"}, "action": {"type": "string", "enum": ["update", "delete"]}, "title": {"type": "string"}, "prompt": {"type": "string"}, "rrule": {"type": "string"}, "target": target_schema, "active": {"type": "boolean"}, "expectedUpdatedAt": {"type": "string"}}, "required": ["id", "action"], "additionalProperties": False}, edit, kind="schedule"),
        ]
        self._tools = tools
        for tool in tools:
            registry.register(tool)
        prompt.register("tool:schedule", 1850, SCHEDULE_GUIDANCE)

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        assert isinstance(registry, ToolRegistry) and isinstance(prompt, SystemPrompt)
        for tool in self._tools:
            registry.unregister(tool.name)
        prompt.unregister("tool:schedule")
