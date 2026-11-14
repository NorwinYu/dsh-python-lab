import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .cancellation import CancellationToken, RunCancelled
from .plugins import Plugin, ServiceContext
from .session import SessionStore
from .subagents import SubagentService
from .system_prompt import SystemPrompt
from .tools import Execution, Tool, ToolError, ToolRegistry


WORKFLOW_GUIDANCE = (
    "Use the workflow tool ONLY when the user explicitly asks for a workflow or for large multi-agent orchestration: "
    "you write a JavaScript script (the tool description documents the exact format) that fans work out across many "
    "subagents with phases and structured results. For one or two delegations, prefer plain subagent calls."
)
WORKFLOW_DESCRIPTION = """Run a JavaScript workflow script that orchestrates subagents at scale. Use this for work that fans out across many independent pieces — an audit over many files, a migration, multi-angle research, adversarial verification of findings — where you write the orchestration as a script instead of delegating turn by turn.

Script-body hooks:
- `agent(prompt, opts?): Promise<any>` — run one subagent to completion. Without `opts.schema` it resolves to the child's final text; with `opts.schema` (an object-rooted JSON Schema using ONLY type/properties/required/additionalProperties/items/enum/const/oneOf) it resolves to the validated object. Resolves `null` when the child fails (filter with `.filter(Boolean)`). Other opts: `label` (display), `phase` (progress group), and independent `provider`/`model` LLM target overrides.
- `pipeline(items, ...stages): Promise<any[]>` — run each item through the stages independently with NO barrier between stages (prefer this for multi-stage work). Each stage receives `(prev, item, index)`. A stage throw drops that ITEM to `null` and skips its remaining stages.
- `parallel(thunks): Promise<any[]>` — run zero-argument functions concurrently and await ALL of them (a barrier; use only when a stage genuinely needs every prior result together). A throwing thunk resolves to `null`.
- `phase(title)` — start a progress phase; `log(message)` — narrate progress; `args` — the tool call's `args` input, verbatim.

Misused hooks (bad arguments, unknown options, unsupported schemas, tripped caps) end the whole script instead of producing `null`. The script has no filesystem, network, timer, or Node.js APIs; the agents do the work."""


class WorkflowError(ToolError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def _plain_json(value: object, path: str = "args") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not (-float("inf") < value < float("inf")):
            raise WorkflowError(f"{path} contains a non-finite number", "RESULT_UNSERIALIZABLE")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _plain_json(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise WorkflowError(f"{path} contains a non-string object key", "RESULT_UNSERIALIZABLE")
            _plain_json(item, f"{path}.{key}")
        return
    raise WorkflowError(f"{path} is not plain JSON", "RESULT_UNSERIALIZABLE")


def _validate_schema(schema: object, root: bool = True) -> None:
    allowed = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "oneOf"}
    if not isinstance(schema, dict) or set(schema) - allowed:
        raise WorkflowError("unsupported JSON schema", "UNSUPPORTED_SCHEMA")
    if root and schema.get("type") != "object":
        raise WorkflowError("agent output schema must be object-rooted", "UNSUPPORTED_SCHEMA")
    if "type" in schema and schema["type"] not in {"object", "array", "string", "number", "integer", "boolean", "null"}:
        raise WorkflowError("unsupported JSON schema type", "UNSUPPORTED_SCHEMA")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise WorkflowError("schema properties must be an object", "UNSUPPORTED_SCHEMA")
    for child in properties.values():
        _validate_schema(child, False)
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise WorkflowError("schema required must be a string array", "UNSUPPORTED_SCHEMA")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        _validate_schema(schema["additionalProperties"], False)
    if "items" in schema:
        _validate_schema(schema["items"], False)
    if "oneOf" in schema:
        if not isinstance(schema["oneOf"], list) or not schema["oneOf"]:
            raise WorkflowError("schema oneOf must be a non-empty array", "UNSUPPORTED_SCHEMA")
        for child in schema["oneOf"]:
            _validate_schema(child, False)
    if "enum" in schema:
        if not isinstance(schema["enum"], list) or not schema["enum"]:
            raise WorkflowError("schema enum must be a non-empty array", "UNSUPPORTED_SCHEMA")
        _plain_json(schema["enum"])
    if "const" in schema:
        _plain_json(schema["const"])


def _matches_schema(value: object, schema: Dict[str, object]) -> bool:
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "oneOf" in schema:
        return sum(_matches_schema(value, child) for child in schema["oneOf"]) == 1
    expected = schema.get("type")
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list) and all(_matches_schema(item, schema["items"]) for item in value) if "items" in schema else isinstance(value, list)
    if expected == "object":
        if not isinstance(value, dict):
            return False
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if any(key not in value for key in required):
            return False
        for key, child in properties.items():
            if key in value and not _matches_schema(value[key], child):
                return False
        additional = schema.get("additionalProperties", True)
        for key in set(value) - set(properties):
            if additional is False:
                return False
            if isinstance(additional, dict) and not _matches_schema(value[key], additional):
                return False
    return True


@dataclass
class _Member:
    request_id: int
    seq: int
    opts: Dict[str, object]
    prompt: str
    child_id: Optional[str] = None


class WorkflowRun:
    def __init__(
        self,
        service: "WorkflowService",
        parent_id: str,
        script: str,
        meta: Dict[str, object],
        args: object,
    ) -> None:
        self.service = service
        self.parent_id = parent_id
        self.script = script
        self.meta = meta
        self.args = args
        self.id = f"workflow-{uuid.uuid4().hex}"
        self._condition = threading.Condition()
        self._result: Optional[Dict[str, object]] = None
        self._process: Optional[subprocess.Popen] = None
        self._members: Dict[str, _Member] = {}
        self._cancel_reason: Optional[str] = None
        self._disposed = False
        self._thread = threading.Thread(target=self._execute, daemon=True)

    def begin(self) -> None:
        self.service._register(self)
        self._thread.start()

    @property
    def result(self) -> Optional[Dict[str, object]]:
        with self._condition:
            return dict(self._result) if self._result is not None else None

    def wait(self, token: Optional[CancellationToken] = None) -> Dict[str, object]:
        with self._condition:
            while self._result is None:
                if token is not None and token.cancelled:
                    self.cancel("parent step aborted")
                    token.throw_if_cancelled()
                self._condition.wait(0.1)
            return dict(self._result)

    def cancel(self, reason: str = "workflow cancelled") -> None:
        with self._condition:
            if self._result is not None:
                return
            self._cancel_reason = reason
            process = self._process
            members = list(self._members.values())
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for member in members:
            if member.child_id is not None:
                self.service._interrupt_child(self.parent_id, member.child_id)

    def dispose(self) -> Dict[str, object]:
        if self.result is None:
            self.cancel("workflow disposed before completion")
        self._thread.join()
        with self._condition:
            self._disposed = True
        self.service._unregister(self.id)
        assert self._result is not None
        return dict(self._result)

    def _execute(self) -> None:
        messages: queue.Queue = queue.Queue()
        errors: List[str] = []
        members: Dict[str, _Member] = {}
        pending: List[_Member] = []
        request_members: Dict[int, _Member] = {}
        sequence = 0
        completed = False
        stop_reason = "completed"
        value: object = None
        error_message: Optional[str] = None
        try:
            node = shutil.which("node")
            if node is None:
                raise WorkflowError("workflow requires a Node.js executable", "RUNTIME_UNAVAILABLE")
            process = subprocess.Popen(
                [node, str(Path(__file__).with_name("workflow_guest.js"))],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={},
                start_new_session=True,
            )
            with self._condition:
                self._process = process
                cancelled = self._cancel_reason is not None
            if cancelled:
                self.cancel(self._cancel_reason or "workflow cancelled")
            threading.Thread(target=self._read_stdout, args=(process, messages), daemon=True).start()
            threading.Thread(target=self._read_stderr, args=(process, errors), daemon=True).start()
            assert process.stdin is not None
            process.stdin.write((json.dumps({"type": "init", "script": self.script, "args": self.args}, allow_nan=False) + "\n").encode())
            process.stdin.flush()
            self._emit("workflow/start", {"id": self.id, "meta": self.meta})
            self.service.session.append(self.parent_id, "tool-workflow/run-start", {"runId": self.id, "name": self.meta["name"]})

            while not completed:
                if self._cancel_reason is not None:
                    stop_reason = "cancelled"
                    error_message = self._cancel_reason
                    break
                self._settle_members(members, request_members, pending)
                while pending and len(members) < self.service.max_concurrent:
                    self._start_member(pending.pop(0), members, request_members, process)
                try:
                    message = messages.get(timeout=0.02)
                except queue.Empty:
                    if process.poll() is not None:
                        stop_reason = "cancelled" if self._cancel_reason is not None else "error"
                        error_message = self._cancel_reason or "workflow runtime exited without a result" + (f": {errors[-1]}" if errors else "")
                        completed = True
                    continue
                message_type = message.get("type")
                if message_type == "agent":
                    sequence += 1
                    if sequence > self.service.max_total_agents:
                        self._reply(process, int(message["id"]), error={"code": "AGENT_CAP", "message": "maximum agent count exceeded", "fatal": True})
                        continue
                    opts = message.get("opts", {})
                    member = _Member(int(message["id"]), sequence, opts, str(message.get("prompt", "")))
                    if len(pending) + len(members) >= self.service.max_total_agents:
                        self._reply(process, member.request_id, error={"code": "AGENT_CAP", "message": "maximum agent count exceeded", "fatal": True})
                    else:
                        pending.append(member)
                elif message_type in {"phase", "log"}:
                    key = "title" if message_type == "phase" else "message"
                    self._emit(f"workflow/{message_type}", {"id": self.id, "meta": self.meta, key: message.get(key)})
                elif message_type == "done":
                    if process.stdin is not None:
                        process.stdin.close()
                    stop_reason = str(message.get("stopReason", "error"))
                    value = message.get("value")
                    if stop_reason == "completed":
                        try:
                            _plain_json(value, "result")
                        except WorkflowError as exc:
                            stop_reason = "error"
                            error_message = str(exc)
                    else:
                        detail = message.get("error", {})
                        error_message = str(detail.get("message", "unknown workflow error")) if isinstance(detail, dict) else str(detail)
                    completed = True
            if stop_reason != "completed":
                self.cancel(error_message or "workflow failed")
            self._settle_members(members, request_members, pending, final=True)
            process.wait(timeout=5)
        except Exception as exc:
            stop_reason = "cancelled" if self._cancel_reason is not None else "error"
            error_message = str(exc)
            self.cancel(error_message)
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            while members:
                self._settle_members(members, request_members, pending, final=True)
                if members:
                    time.sleep(0.01)
            pending.clear()
            process = self._process
            if process is not None:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    error_message = "workflow runtime did not exit"
                    stop_reason = "error"
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
            self._finish(stop_reason, value, error_message, sequence)

    @staticmethod
    def _read_stdout(process: subprocess.Popen, messages: queue.Queue) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            if len(line) > 1_000_000:
                messages.put({"type": "done", "stopReason": "error", "error": {"message": "workflow control message too large"}})
                return
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                messages.put({"type": "done", "stopReason": "error", "error": {"message": "invalid workflow control message"}})
                continue
            if isinstance(message, dict):
                messages.put(message)

    @staticmethod
    def _read_stderr(process: subprocess.Popen, errors: List[str]) -> None:
        assert process.stderr is not None
        data = process.stderr.read(65537)
        if data:
            errors.append(data[:65536].decode("utf-8", errors="replace"))

    def _start_member(self, member: _Member, members: Dict[str, _Member], requests: Dict[int, _Member], process: subprocess.Popen) -> None:
        opts = member.opts
        try:
            if set(opts) - {"label", "phase", "schema", "provider", "model"}:
                raise WorkflowError("unknown agent option", "UNSUPPORTED_OPTION")
            if opts.get("provider") not in {None, "mock"} or opts.get("model") not in {None, getattr(self.service.subagents.agent.model, "model_id", "mock")}:
                raise WorkflowError("provider/model overrides require a selectable model route", "UNSUPPORTED_OPTION")
            schema = opts.get("schema")
            if schema is not None:
                _validate_schema(schema)
            started = self.service.subagents.start(
                self.parent_id,
                member.prompt,
                str(opts.get("label") or f"workflow agent {member.seq}"),
                "spawn",
                delivery="caller",
            )
            member.child_id = str(started["childId"])
            members[member.child_id] = member
            requests[member.request_id] = member
            with self._condition:
                self._members[member.child_id] = member
            info = {"id": self.id, "meta": self.meta, "seq": member.seq, "label": opts.get("label"), "phase": opts.get("phase"), "childId": member.child_id}
            self._emit("workflow/agent-start", info)
            self.service.session.append(self.parent_id, "tool-workflow/agent-start", {"runId": self.id, "seq": member.seq, "label": opts.get("label"), "phase": opts.get("phase"), "childId": member.child_id})
        except Exception as exc:
            self._reply(process, member.request_id, error={"code": getattr(exc, "code", "AGENT_START"), "message": str(exc), "fatal": True})

    def _settle_members(self, members: Dict[str, _Member], requests: Dict[int, _Member], pending: List[_Member], final: bool = False) -> None:
        process = self._process
        for child_id, member in list(members.items()):
            record = self.service.subagents.record(child_id)
            if record["status"] == "running":
                if final:
                    self.service._interrupt_child(self.parent_id, child_id)
                continue
            outcome = "cancelled" if record["stopReason"] == "aborted" else ("completed" if record["stopReason"] == "completed" else "error")
            result: object = None
            if outcome == "completed":
                schema = member.opts.get("schema")
                if schema is None:
                    result = record["output"]
                else:
                    try:
                        candidate = json.loads(str(record["output"]))
                        if _matches_schema(candidate, schema):
                            result = candidate
                    except (json.JSONDecodeError, TypeError):
                        pass
            if process is not None and process.poll() is None:
                self._reply(process, member.request_id, value=result)
            self._emit("workflow/agent-end", {"id": self.id, "meta": self.meta, "seq": member.seq, "outcome": outcome})
            self.service.session.append(self.parent_id, "tool-workflow/agent-end", {"runId": self.id, "seq": member.seq, "outcome": outcome})
            members.pop(child_id, None)
            requests.pop(member.request_id, None)
            with self._condition:
                self._members.pop(child_id, None)
        if final:
            pending.clear()

    @staticmethod
    def _reply(process: subprocess.Popen, request_id: int, value: object = None, error: Optional[Dict[str, object]] = None) -> None:
        if process.stdin is None or process.poll() is not None:
            return
        message: Dict[str, object] = {"type": "result", "id": request_id}
        if error is None:
            message["value"] = value
        else:
            message["error"] = error
        try:
            process.stdin.write((json.dumps(message, allow_nan=False) + "\n").encode())
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _emit(self, event: str, payload: Dict[str, object]) -> None:
        try:
            payload.setdefault("session_id", self.parent_id)
            payload.setdefault("root_session_id", self.parent_id)
            self.service.ctx.events.emit(event, payload)
        except Exception:
            pass

    def _finish(self, stop_reason: str, value: object, error: Optional[str], count: int) -> None:
        result: Dict[str, object] = {"runId": self.id, "stopReason": stop_reason, "value": value, "agentsStarted": count}
        if error is not None:
            result["error"] = error
        with self._condition:
            if self._result is not None:
                return
            self._result = result
            self._condition.notify_all()
        self._emit("workflow/end", {"id": self.id, "meta": self.meta, "stopReason": stop_reason, "error": error, "agentsStarted": count})
        try:
            self.service.session.append(self.parent_id, "tool-workflow/run-end", {"runId": self.id, "stopReason": stop_reason})
        except Exception:
            pass


class WorkflowService:
    def __init__(self, ctx: ServiceContext, subagents: SubagentService, session: SessionStore) -> None:
        self.ctx = ctx
        self.subagents = subagents
        self.session = session
        self.max_total_agents = self._limit("MINIDSH_WORKFLOW_MAX_AGENTS", 1000)
        self.max_concurrent = self._limit("MINIDSH_WORKFLOW_MAX_CONCURRENT", min(32, os.cpu_count() or 1))
        self._runs: Dict[str, WorkflowRun] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _limit(name: str, default: int) -> int:
        try:
            value = int(os.environ.get(name, str(default)))
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive integer") from exc
        if value < 1:
            raise ValueError(f"{name} must be a positive integer")
        return value

    def start(self, parent_id: Optional[str], script: object, meta: object, args: object = None) -> WorkflowRun:
        if not parent_id:
            raise WorkflowError("workflow tool requires a calling agent", "CALLER_REQUIRED")
        if not isinstance(script, str) or not script.strip():
            raise WorkflowError("script must be a non-empty string", "SCRIPT_PARSE")
        if not isinstance(meta, dict) or not isinstance(meta.get("name"), str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(meta.get("name"))):
            raise WorkflowError("meta.name must be a short kebab-case string", "META_INVALID")
        if not isinstance(meta.get("description"), str) or not str(meta.get("description")).strip():
            raise WorkflowError("meta.description must be a non-empty string", "META_INVALID")
        phases = meta.get("phases", [])
        if not isinstance(phases, list) or any(not isinstance(phase, dict) or not isinstance(phase.get("title"), str) for phase in phases):
            raise WorkflowError("meta.phases must contain objects with string titles", "META_INVALID")
        _plain_json(meta, "meta")
        _plain_json(args, "args")
        run = WorkflowRun(self, parent_id, script, dict(meta), args)
        run.begin()
        return run

    def _register(self, run: WorkflowRun) -> None:
        with self._lock:
            self._runs[run.id] = run

    def _unregister(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)

    def _interrupt_child(self, parent_id: str, child_id: str) -> None:
        try:
            self.subagents.interrupt(parent_id, child_id)
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            run.cancel("application closing")
        for run in runs:
            run.dispose()


class WorkflowPlugin(Plugin):
    name = "core.workflow"
    provides = ("workflowEngine",)
    requires = ("subagents", "session")

    def __init__(self) -> None:
        self.service: Optional[WorkflowService] = None

    def activate(self, ctx: ServiceContext) -> None:
        subagents = ctx.require("subagents")
        session = ctx.require("session")
        assert isinstance(subagents, SubagentService) and isinstance(session, SessionStore)
        self.service = WorkflowService(ctx, subagents, session)
        ctx.provide("workflowEngine", self.service, self.name)

    def deactivate(self, ctx: ServiceContext) -> None:
        if self.service is not None:
            self.service.close()


class WorkflowToolPlugin(Plugin):
    name = "tools.workflow"
    requires = ("tools", "systemPrompt", "workflowEngine")

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        engine = ctx.require("workflowEngine")
        assert isinstance(registry, ToolRegistry) and isinstance(prompt, SystemPrompt) and isinstance(engine, WorkflowService)

        def execute(arguments: Dict[str, object], execution: Execution) -> str:
            run = engine.start(execution.session_id, arguments.get("script"), arguments.get("meta"), arguments.get("args"))
            try:
                result = run.wait(execution.cancel_token)
                if result["stopReason"] != "completed":
                    raise ToolError(f"workflow run {result['stopReason']}: {result.get('error', 'unknown error')}")
                rendered = json.dumps(result["value"], ensure_ascii=False, indent=2, allow_nan=False)
                if len(rendered) > 50000:
                    rendered = rendered[:50000] + f"\n… [truncated: {len(rendered) - 50000} more characters]"
                return f"workflow \"{arguments['meta']['name']}\" completed ({result['agentsStarted']} agents).\nReturn value:\n{rendered}"
            finally:
                run.dispose()

        tool = Tool(
            "workflow",
            WORKFLOW_DESCRIPTION,
            {
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "The plain JavaScript body, not TypeScript and without an `export const meta` statement; top-level await is allowed. End with `return <value>`; the JSON-serializable value is this tool's result."},
                    "meta": {"type": "object", "description": "The workflow identity as plain JSON, not code.", "properties": {"name": {"type": "string"}, "description": {"type": "string"}, "whenToUse": {"type": "string"}, "phases": {"type": "array"}}, "required": ["name", "description"]},
                    "args": {"type": "object", "description": "Optional JSON input exposed to the script as the `args` global."},
                },
                "required": ["script", "meta"],
                "additionalProperties": False,
            },
            execute,
            kind="workflow",
        )
        registry.register(tool)
        prompt.register("tool:workflow", 1800, WORKFLOW_GUIDANCE)

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        assert isinstance(registry, ToolRegistry) and isinstance(prompt, SystemPrompt)
        registry.unregister("workflow")
        prompt.unregister("tool:workflow")
