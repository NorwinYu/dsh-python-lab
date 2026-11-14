import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .cancellation import CancellationToken, RunCancelled
from .llm import text_block
from .plugins import Plugin, ServiceContext
from .session import SessionStore
from .subagents import SubagentService
from .system_prompt import SystemPrompt
from .tools import Execution, Tool, ToolError, ToolRegistry


GOAL_GUIDANCE = (
    "create_goal may infer goal intent from a direct human request in any language. After session resume or fork, an active goal is disarmed: when a human asks to continue or resume in any wording or language, use update_goal action resume to rearm it. Mark complete only when the objective is actually achieved. Mark blocked only after the same blocking condition persists for at least 3 consecutive goal rounds, and report that concrete condition in blocked_reason; difficulty, uncertainty, or useful remaining work is not blocked."
)
PHASES = {"active", "paused", "blocked", "complete"}
ACTIONS = {"edit", "pause", "resume", "complete", "blocked", "clear"}


class GoalError(ToolError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class GoalService:
    def __init__(self, ctx: ServiceContext, session: SessionStore, default_rounds: int = 256) -> None:
        if not isinstance(default_rounds, int) or isinstance(default_rounds, bool) or default_rounds < 1:
            raise ValueError("MINIDSH_GOAL_MAX_ROUNDS must be a positive integer")
        self.ctx = ctx
        self.session = session
        self.default_rounds = default_rounds
        self._states: Dict[str, Dict[str, object]] = {}
        self._armed: Dict[str, bool] = {}
        self._turn_sources: Dict[str, str] = {}
        self._round_running: Dict[str, bool] = {}
        self._round_tokens: Dict[str, CancellationToken] = {}
        self._wrapups: Dict[str, List[Dict[str, object]]] = {}
        self._lock = threading.RLock()

    def note_turn(self, session_id: str, source: Optional[Dict[str, object]]) -> None:
        kind = str(source.get("kind", "user")) if isinstance(source, dict) else "user"
        with self._lock:
            self._turn_sources[session_id] = kind

    def _fold(self, session_id: str) -> Optional[Dict[str, object]]:
        current: Optional[Dict[str, object]] = None
        rounds: Dict[str, int] = {}
        for event in self.session.load(session_id):
            if event["type"] == "goal/change":
                payload = event["payload"]
                if payload.get("version") != 1 or "goal" not in payload:
                    raise GoalError("GOAL_REPLAY_INVALID", "invalid goal/change payload")
                snapshot = payload["goal"]
                if snapshot is None:
                    if current is None or payload.get("revision") != int(current["revision"]) + 1:
                        raise GoalError("GOAL_REPLAY_INVALID", "invalid goal tombstone")
                    current = None
                    continue
                if not isinstance(snapshot, dict) or snapshot.get("phase") not in PHASES:
                    raise GoalError("GOAL_REPLAY_INVALID", "invalid goal phase")
                if not isinstance(snapshot.get("id"), str) or not snapshot["id"] or not isinstance(snapshot.get("objective"), str) or not snapshot["objective"].strip():
                    raise GoalError("GOAL_REPLAY_INVALID", "invalid goal identity")
                revision = snapshot.get("revision")
                if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                    raise GoalError("GOAL_REPLAY_INVALID", "invalid goal revision")
                if current is not None:
                    if current["id"] == snapshot["id"] and revision != int(current["revision"]) + 1:
                        raise GoalError("GOAL_REPLAY_INVALID", "non-contiguous goal revision")
                    if current["phase"] != "complete" and current["id"] != snapshot["id"]:
                        raise GoalError("GOAL_REPLAY_INVALID", "active goal was replaced")
                    if current["id"] == snapshot["id"] and str(snapshot.get("updatedAt", "")) < str(current.get("updatedAt", "")):
                        raise GoalError("GOAL_REPLAY_INVALID", "goal timestamps moved backwards")
                snapshot = dict(snapshot)
                snapshot["roundsStarted"] = rounds.get(snapshot["id"], 0)
                current = snapshot
            elif event["type"] == "user/message" and current is not None:
                source = event["payload"].get("source", {})
                if not isinstance(source, dict) or source.get("kind") != "goal" or source.get("goalId") != current["id"]:
                    continue
                round_number = source.get("round")
                expected = rounds.get(current["id"], 0) + 1
                if not isinstance(round_number, int) or isinstance(round_number, bool) or round_number != expected:
                    raise GoalError("GOAL_REPLAY_INVALID", "non-contiguous goal round")
                revision = source.get("revision")
                if not isinstance(revision, int) or revision > int(current["revision"]):
                    raise GoalError("GOAL_REPLAY_INVALID", "goal round references future revision")
                rounds[current["id"]] = expected
                current["roundsStarted"] = expected
        return current

    def get(self, session_id: str) -> Optional[Dict[str, object]]:
        with self._lock:
            goal = self._fold(session_id)
            if goal is None:
                return None
            result = dict(goal)
            result["activation"] = "armed" if self._armed.get(session_id, False) else "disarmed"
            return result

    def _view(self, goal: Dict[str, object], armed: bool) -> Dict[str, object]:
        result = dict(goal)
        result["activation"] = "armed" if armed else "disarmed"
        return result

    def create(self, session_id: str, objective: object, max_rounds: Optional[int] = None) -> Dict[str, object]:
        if not isinstance(objective, str) or not objective.strip():
            raise GoalError("GOAL_INVALID_OBJECTIVE", "objective must be non-empty")
        rounds = self.default_rounds if max_rounds is None else max_rounds
        if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1:
            raise GoalError("GOAL_INVALID_BUDGET", "max_goal_rounds must be positive")
        with self._lock:
            current = self._fold(session_id)
            if current is not None and current["phase"] != "complete":
                raise GoalError("GOAL_ALREADY_ACTIVE", "replace, clear, or complete the current goal first")
            goal = {
                "id": uuid.uuid4().hex,
                "revision": 1,
                "objective": objective.strip(),
                "phase": "active",
                "roundsStarted": 0,
                "maxGoalRounds": rounds,
                "updatedAt": timestamp(),
            }
            self._armed[session_id] = True
            self._states[session_id] = goal
            self._append(session_id, goal)
            return self._view(goal, True)

    def _append(self, session_id: str, goal: Optional[Dict[str, object]], revision: Optional[int] = None) -> None:
        self.session.append(session_id, "goal/change", {"version": 1, "goal": goal, "revision": revision if revision is not None else goal["revision"] if goal else 0})
        self.ctx.events.emit("goal/changed", {"session_id": session_id, "goal": self._view(goal, self._armed.get(session_id, False)) if goal else None})

    def update(
        self,
        session_id: str,
        goal_id: str,
        revision: int,
        action: str,
        objective: Optional[str] = None,
        max_rounds: Optional[int] = None,
        blocked_reason: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, object]]:
        if action not in ACTIONS:
            raise GoalError("GOAL_INVALID_ACTION", "unknown goal action")
        with self._lock:
            current = self._fold(session_id)
            if current is None or current["id"] != goal_id or current["revision"] != revision:
                raise GoalError("GOAL_STALE_REVISION", "goal id or revision is stale")
            if action == "clear":
                next_revision = revision + 1
                self._armed[session_id] = False
                self._append(session_id, None, next_revision)
                self._states.pop(session_id, None)
                return None
            updated = dict(current)
            updated["revision"] = revision + 1
            updated["updatedAt"] = timestamp()
            if action == "edit":
                if objective is not None:
                    if not objective.strip():
                        raise GoalError("GOAL_INVALID_OBJECTIVE", "objective must be non-empty")
                    updated["objective"] = objective.strip()
                if max_rounds is not None:
                    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds < 1:
                        raise GoalError("GOAL_INVALID_BUDGET", "max_goal_rounds must be positive")
                    updated["maxGoalRounds"] = max_rounds
                if objective is None and max_rounds is None:
                    raise GoalError("GOAL_INVALID_ACTION", "edit requires objective or max_goal_rounds")
            elif action == "pause":
                updated["phase"] = "paused"
                self._armed[session_id] = False
            elif action == "resume":
                if int(current["roundsStarted"]) >= int(current["maxGoalRounds"]):
                    raise GoalError("GOAL_ROUND_LIMIT", "goal has no remaining rounds")
                updated["phase"] = "active"
                updated.pop("blockedReason", None)
                self._armed[session_id] = True
            elif action == "complete":
                updated["phase"] = "complete"
                self._armed[session_id] = False
            elif action == "blocked":
                if not isinstance(blocked_reason, dict) or not str(blocked_reason.get("message", "")).strip():
                    raise GoalError("GOAL_INVALID_REASON", "blocked_reason is required")
                updated["phase"] = "blocked"
                updated["blockedReason"] = {"code": str(blocked_reason.get("code", "model-reported")), "message": str(blocked_reason["message"])}
                self._armed[session_id] = False
            updated["roundsStarted"] = self._fold(session_id)["roundsStarted"]
            self._states[session_id] = updated
            self._append(session_id, updated)
            return self._view(updated, self._armed.get(session_id, False))

    def disarm(self, session_id: str) -> None:
        with self._lock:
            self._armed[session_id] = False
            token = self._round_tokens.get(session_id)
            if token is not None:
                token.cancel()
            self.ctx.events.emit("goal/activation-changed", {"session_id": session_id, "activation": "disarmed"})

    def authority(self, session_id: str, action: str) -> str:
        subagents = self.ctx.optional("subagents")
        if isinstance(subagents, SubagentService):
            try:
                if subagents.record(session_id)["depth"] > 0:
                    raise GoalError("GOAL_SUBAGENT", "A delegated subagent cannot change goals.")
            except GoalError:
                raise
            except Exception:
                pass
        source = self._turn_sources.get(session_id, "external")
        if action in {"create", "edit", "pause", "resume"} and source != "user":
            raise GoalError("GOAL_HUMAN_REQUIRED", "this goal action requires a direct human turn")
        if action == "resume":
            current = self._fold(session_id)
            if current is not None and current["phase"] == "paused":
                raise GoalError("GOAL_TOOL_RESUME_PAUSED", "resume a paused goal through the user control")
        return source

    def begin_round(self, session_id: str) -> Optional[Dict[str, object]]:
        with self._lock:
            current = self._fold(session_id)
            if current is None or current["phase"] != "active" or not self._armed.get(session_id, False) or self._round_running.get(session_id, False):
                return None
            if int(current["roundsStarted"]) >= int(current["maxGoalRounds"]):
                self.update(session_id, str(current["id"]), int(current["revision"]), "blocked", blocked_reason={"code": "round-limit", "message": "goal round limit reached"})
                return None
            round_number = int(current["roundsStarted"]) + 1
            self._round_running[session_id] = True
            token = CancellationToken(f"goal-{current['id']}-{round_number}")
            self._round_tokens[session_id] = token
            source = {"kind": "goal", "goalId": current["id"], "revision": current["revision"], "round": round_number}
            prompt = (
                "<goal_round>\n"
                + json.dumps({"objective": current["objective"], "round": round_number, "maxGoalRounds": current["maxGoalRounds"]}, ensure_ascii=False)
                + "\nUse the current workspace, tool results, and persisted state as the source of truth. Continue the objective without repeating settled work. Mark complete only when it is actually achieved; mark blocked only for a persistent concrete blocker.\n</goal_round>"
            )
            return {"prompt": prompt, "source": source, "token": token}

    def finish_round(self, session_id: str, reason: object = None) -> None:
        with self._lock:
            self._round_running[session_id] = False
            self._round_tokens.pop(session_id, None)
            current = self._fold(session_id)
            if current is None:
                self._armed[session_id] = False
                return
            if current["phase"] != "active":
                self._armed[session_id] = False
                return
            if isinstance(reason, dict) and reason.get("kind") in {"aborted", "max-tokens", "error"}:
                self._armed[session_id] = False
                return
            if int(current["roundsStarted"]) >= int(current["maxGoalRounds"]):
                self.update(session_id, str(current["id"]), int(current["revision"]), "blocked", blocked_reason={"code": "round-limit", "message": "goal round limit reached"})
                return
            self._armed[session_id] = True

    def wrapup(self, session_id: str, phase: str) -> None:
        tag = "goal_complete" if phase == "complete" else "goal_blocked"
        with self._lock:
            self._wrapups.setdefault(session_id, []).append({
                "role": "user",
                "content": [text_block(f"<{tag}>\nWrite the final user-facing summary now. Do not start more goal work.\n</{tag}>")],
                "source": {"kind": "goal-wrapup", "phase": phase},
            })

    def take_wrapups(self, session_id: str) -> List[Dict[str, object]]:
        with self._lock:
            return self._wrapups.pop(session_id, [])


class GoalPlugin(Plugin):
    name = "core.goals"
    provides = ("goals",)
    requires = ("session",)

    def activate(self, ctx: ServiceContext) -> None:
        import os
        session = ctx.require("session")
        assert isinstance(session, SessionStore)
        try:
            rounds = int(os.environ.get("MINIDSH_GOAL_MAX_ROUNDS", "256"))
        except ValueError as exc:
            raise ValueError("MINIDSH_GOAL_MAX_ROUNDS must be an integer") from exc
        ctx.provide("goals", GoalService(ctx, session, rounds), self.name)


class GoalToolPlugin(Plugin):
    name = "tools.goal"
    requires = ("tools", "systemPrompt", "goals")

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        goals = ctx.require("goals")
        assert isinstance(registry, ToolRegistry) and isinstance(prompt, SystemPrompt) and isinstance(goals, GoalService)

        def get(arguments: Dict[str, object], execution: Execution) -> str:
            if not execution.session_id:
                raise GoalError("GOAL_CALLER_REQUIRED", "get_goal requires a session")
            return json.dumps({"goal": goals.get(execution.session_id)}, ensure_ascii=False)

        def create(arguments: Dict[str, object], execution: Execution) -> str:
            if not execution.session_id:
                raise GoalError("GOAL_CALLER_REQUIRED", "create_goal requires a session")
            goals.authority(execution.session_id, "create")
            goal = goals.create(execution.session_id, arguments.get("objective"), arguments.get("max_goal_rounds"))
            return json.dumps({"goal": goal}, ensure_ascii=False)

        def update(arguments: Dict[str, object], execution: Execution) -> str:
            if not execution.session_id:
                raise GoalError("GOAL_CALLER_REQUIRED", "update_goal requires a session")
            action = arguments.get("action")
            if not isinstance(action, str):
                raise GoalError("GOAL_INVALID_ACTION", "action is required")
            source = goals.authority(execution.session_id, action)
            goal_id, revision = arguments.get("goal_id"), arguments.get("revision")
            if not isinstance(goal_id, str) or not isinstance(revision, int) or isinstance(revision, bool):
                raise GoalError("GOAL_STALE_REVISION", "goal_id and integer revision are required")
            if action == "clear":
                raise GoalError("GOAL_INVALID_ACTION", "clear is available through user controls")
            if action == "blocked":
                reason = arguments.get("blocked_reason")
                if source == "goal":
                    current = goals.get(execution.session_id)
                    if current is None or int(current["roundsStarted"]) < 3:
                        raise GoalError("GOAL_TOOL_BLOCKED_EARLY", "blocked requires at least 3 consecutive goal rounds")
                if not isinstance(reason, str) or not reason.strip():
                    raise GoalError("GOAL_INVALID_REASON", "blocked_reason must be non-empty")
                blocked = {"code": "model-reported", "message": reason.strip()}
            else:
                blocked = None
            objective = arguments.get("objective") if action == "edit" and arguments.get("objective") else None
            max_rounds = arguments.get("max_goal_rounds") if action == "edit" and arguments.get("max_goal_rounds") else None
            goal = goals.update(execution.session_id, goal_id, revision, action, objective, max_rounds, blocked)
            if source == "goal" and action in {"complete", "blocked"}:
                goals.wrapup(execution.session_id, action)
            return json.dumps({"goal": goal}, ensure_ascii=False)

        registry.register(Tool("get_goal", "Read the current persistent goal, or null when no goal exists.", {"type": "object", "properties": {}, "additionalProperties": False}, get, kind="goal"))
        registry.register(Tool("create_goal", "Create one persistent goal for a substantial direct human request.", {"type": "object", "properties": {"objective": {"type": "string"}, "max_goal_rounds": {"type": "integer", "description": "Positive maximum number of goal rounds; defaults to 256."}}, "required": ["objective"], "additionalProperties": False}, create, kind="goal"))
        registry.register(Tool("update_goal", "Update a goal by exact id and revision. Read get_goal first. Actions: edit, pause, resume, complete, blocked.", {"type": "object", "properties": {"goal_id": {"type": "string"}, "revision": {"type": "integer"}, "action": {"type": "string", "enum": sorted(ACTIONS - {"clear"})}, "objective": {"type": "string"}, "max_goal_rounds": {"type": "integer"}, "blocked_reason": {"type": "string"}}, "required": ["goal_id", "revision", "action"], "additionalProperties": False}, update, kind="goal"))
        self._tools = ["get_goal", "create_goal", "update_goal"]
        prompt.register("tool:goal", 1900, GOAL_GUIDANCE)

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        assert isinstance(registry, ToolRegistry) and isinstance(prompt, SystemPrompt)
        for name in self._tools:
            registry.unregister(name)
        prompt.unregister("tool:goal")


class GoalRoundDriver(Plugin):
    name = "core.goal-rounds"
    requires = ("goals",)

    def __init__(self) -> None:
        self.goals: Optional[GoalService] = None
        self.ctx: Optional[ServiceContext] = None
        self._threads: Dict[str, threading.Thread] = {}
        self._closed = False

    def activate(self, ctx: ServiceContext) -> None:
        self.ctx = ctx
        goals = ctx.require("goals")
        assert isinstance(goals, GoalService)
        self.goals = goals
        ctx.events.on("turn/end", self._turn_end)

    def _turn_end(self, payload: Dict[str, object]) -> None:
        if self._closed or self.goals is None:
            return
        session_id = str(payload["session_id"])
        self.goals.finish_round(session_id, payload.get("reason"))
        if session_id in self._threads:
            return
        next_round = self.goals.begin_round(session_id)
        if next_round is None:
            return
        thread = threading.Thread(target=self._run, args=(session_id, next_round), daemon=True)
        self._threads[session_id] = thread
        thread.start()

    def _run(self, session_id: str, request: Dict[str, object]) -> None:
        time.sleep(0.02)
        failed = False
        try:
            assert self.ctx is not None
            agent = self.ctx.require("agent")
            agent.run(request["prompt"], session_id, request["token"].run_id, request["token"], message_source=request["source"])
        except Exception:
            failed = True
            if self.goals is not None:
                self.goals.disarm(session_id)
        finally:
            if self.goals is None:
                return
            with self.goals._lock:
                self._threads.pop(session_id, None)
            if not self._closed:
                self._turn_end({"session_id": session_id, "reason": {"kind": "error" if failed else "stop"}})

    def deactivate(self, ctx: ServiceContext) -> None:
        self._closed = True
        if self.goals is not None:
            for session_id in list(self._threads):
                self.goals.disarm(session_id)
        for thread in list(self._threads.values()):
            thread.join(timeout=5)
