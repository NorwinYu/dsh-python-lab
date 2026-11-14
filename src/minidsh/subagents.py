import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .cancellation import CancellationToken, RunCancelled
from .llm import text_block
from .plugins import Plugin, ServiceContext
from .session import SessionStore
from .system_prompt import SystemPrompt
from .tools import Tool, ToolError, ToolRegistry


NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
ROOT_NAME = "lead"
SUBAGENT_GUIDANCE = (
    "Start independent delegations with `subagent` together in one assistant message and continue useful work "
    "while they run. When only waiting for their results remains, end your turn instead of sleeping or polling; "
    "the next result starts a new turn. Collect the subagent results required to complete the task before giving "
    "the user a final answer.\n\nAgents may use different working directories; changes to shared files affect "
    "other agents. Split parallel write work into disjoint file or directory scopes. Bash, formatters, code "
    "generators, and scripts can overwrite concurrent edits without conflict detection; coordinate their changes "
    "explicitly. The agent integrating the work must review the final diff and run relevant tests."
)
CONTROL_GUIDANCE = (
    "To message other agents, use send_message({ agent: \"<agent-name>\", message: \"...\" }).\n"
    "Use list_agents({}) to find your collaborator agents and their names."
)


class SubagentError(ToolError):
    def __init__(self, message: str, code: str = "SUBAGENT_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ChildRecord:
    child_id: str
    parent_id: str
    root_id: str
    name: str
    label: str
    provider: str
    cwd: str
    depth: int
    created_at: float = field(default_factory=time.time)
    status: str = "inactive"
    stop_reason: Optional[str] = None
    output: str = ""
    diagnostic: Optional[str] = None
    token: CancellationToken = field(default_factory=CancellationToken)
    inbox: List[Tuple[str, str, str]] = field(default_factory=list)
    thread: Optional[threading.Thread] = None


class SubagentService:
    def __init__(self, ctx: ServiceContext, session: SessionStore, max_depth: int = 1) -> None:
        self.ctx = ctx
        self.events = ctx.events
        self.session = session
        self.max_depth = max_depth
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._children: Dict[str, ChildRecord] = {}
        self._parent_inputs: Dict[str, List[Dict[str, object]]] = {}
        self._running_roots: Dict[str, int] = {}
        self._closed = False

    @property
    def agent(self) -> object:
        return self.ctx.require("agent")

    def set_root_running(self, session_id: str, running: bool) -> None:
        with self._changed:
            count = self._running_roots.get(session_id, 0)
            self._running_roots[session_id] = max(0, count + (1 if running else -1))
            if not self._running_roots[session_id]:
                self._running_roots.pop(session_id, None)
            self._changed.notify_all()

    def start(
        self,
        parent_id: Optional[str],
        prompt: str,
        label: str,
        provider: str = "spawn",
        name: Optional[str] = None,
        cwd: Optional[str] = None,
        cancel_token: Optional[CancellationToken] = None,
    ) -> Dict[str, object]:
        if not parent_id:
            raise SubagentError("subagent tool requires a calling agent", "CALLER_REQUIRED")
        if not prompt.strip() or not label.strip():
            raise SubagentError("description and prompt must be non-empty strings", "INVALID_ARGUMENT")
        if provider not in {"spawn", "fork"}:
            raise SubagentError(f"unknown subagent provider: {provider}", "PROVIDER_NOT_FOUND")
        if name is not None and (not NAME_PATTERN.fullmatch(name) or name == ROOT_NAME):
            raise SubagentError("invalid subagent name", "INVALID_NAME")
        if cancel_token is not None:
            cancel_token.throw_if_cancelled()
        with self._changed:
            if self._closed:
                raise SubagentError("subagent service is closed", "CLOSED")
            parent = self._children.get(parent_id)
            depth = (parent.depth if parent is not None else 0) + 1
            if depth > self.max_depth:
                raise SubagentError(
                    f"delegation depth {depth} exceeds maximum {self.max_depth}",
                    "MAX_DEPTH",
                )
            root_id = parent.root_id if parent is not None else parent_id
            siblings = [row for row in self._children.values() if row.parent_id == parent_id]
            if name is None:
                serial = 1
                names = {row.name.rsplit("/", 1)[-1] for row in siblings}
                while f"agent-{serial}" in names:
                    serial += 1
                name = f"agent-{serial}"
            if any(row.name.rsplit("/", 1)[-1] == name for row in siblings):
                raise SubagentError(f"duplicate subagent name: {name}", "DUPLICATE_NAME")
            full_name = name if parent_id == root_id else f"{self._children[parent_id].name}/{name}"
            workspace = Path(self.ctx.require("agent").workspace)
            child_cwd = (workspace / cwd).expanduser().resolve() if cwd else workspace
            try:
                child_cwd.relative_to(workspace)
            except ValueError as exc:
                raise SubagentError("subagent cwd must remain inside the workspace", "INVALID_CWD") from exc
            if not child_cwd.is_dir():
                raise SubagentError(f"subagent cwd is not a directory: {child_cwd}", "INVALID_CWD")
            child_id = uuid.uuid4().hex
            record = ChildRecord(
                child_id=child_id,
                parent_id=parent_id,
                root_id=root_id,
                name=full_name,
                label=label.strip(),
                provider=provider,
                cwd=str(child_cwd),
                depth=depth,
            )
            if provider == "fork":
                inherited = self._completed_turn_prefix(parent_id)
                if inherited is not None:
                    child_id = self.session.fork(parent_id, inherited)
                    record.child_id = child_id
            self._children[child_id] = record
            catalog = {
                "id": child_id,
                "parentId": parent_id,
                "rootId": root_id,
                "name": full_name,
                "label": record.label,
                "provider": provider,
                "mode": "continuable",
                "cwd": record.cwd,
                "depth": depth,
            }
            self.session.append(parent_id, "subagent/catalog", catalog)
            self._start_worker(record, prompt, ROOT_NAME if parent_id == root_id else self._children[parent_id].name, True)
            info = {"session_id": parent_id, "child_id": child_id, "parent_session_id": parent_id,
                    "root_session_id": root_id, "name": full_name, "provider": provider, "label": record.label}
            self.events.emit("subagent/start", info)
            return {"kind": "activation", "name": full_name, "childId": child_id}

    def _completed_turn_prefix(self, parent_id: str) -> Optional[int]:
        ends = [int(event["seq"]) for event in self.session.load(parent_id) if event["type"] == "turn/end"]
        return max(ends) if ends else None

    def _start_worker(self, record: ChildRecord, prompt: str, sender: str, initial: bool = False) -> None:
        message_id = uuid.uuid4().hex
        record.inbox.append((message_id, sender, prompt))
        if record.status == "running":
            self._changed.notify_all()
            return
        record.status = "running"
        record.stop_reason = None
        record.diagnostic = None
        record.token = CancellationToken(record.child_id)
        record.thread = threading.Thread(target=self._run_child, args=(record.child_id, not initial), daemon=True)
        record.thread.start()

    def _run_child(self, child_id: str, resume: bool) -> None:
        with self._changed:
            record = self._children[child_id]
            token = record.token
        stop_reason = "completed"
        diagnostic = None
        output = ""
        try:
            while True:
                with self._changed:
                    pending = list(record.inbox)
                    record.inbox.clear()
                if not pending:
                    break
                prompt = "\n".join(item[2] for item in pending)
                output = self.agent.run(prompt, child_id if resume else None, child_id, token, new_session_id=child_id)
                resume = True
                with self._changed:
                    if not record.inbox:
                        break
        except RunCancelled:
            stop_reason = "aborted"
            diagnostic = "subagent execution was interrupted"
        except Exception as exc:
            stop_reason = "error"
            diagnostic = str(exc)[:4096]
        with self._changed:
            record.status = "inactive"
            record.stop_reason = stop_reason
            record.output = output
            record.diagnostic = diagnostic
            summary = f"Subagent {record.name} {stop_reason}."
            notice = {
                "role": "user",
                "content": [text_block(summary + (f"\n{output}" if output else "") + (f"\n{diagnostic}" if diagnostic else ""))],
                "source": {
                    "kind": "subagent-settled",
                    "form": "notice",
                    "summary": summary,
                    "stopReason": stop_reason,
                    "senderSessionId": child_id,
                },
            }
            self._parent_inputs.setdefault(record.parent_id, []).append(notice)
            self.events.emit("subagent/end", {
                "session_id": record.parent_id,
                "child_id": child_id,
                "parent_session_id": record.parent_id,
                "root_session_id": record.root_id,
                "name": record.name,
                "stop_reason": stop_reason,
                "output": output,
                "diagnostic": diagnostic,
            })
            self._changed.notify_all()

    def consume_inputs(self, session_id: str) -> List[Dict[str, object]]:
        with self._changed:
            record = self._children.get(session_id)
            messages: List[Dict[str, object]] = []
            if record is not None and record.inbox:
                pending = list(record.inbox)
                record.inbox.clear()
                for message_id, sender, text in pending:
                    messages.append({
                        "role": "user",
                        "content": [text_block(text)],
                        "source": {"kind": "agent-message", "form": "relay", "senderSessionId": sender,
                                   "messageId": message_id},
                    })
            messages.extend(self._parent_inputs.pop(session_id, []))
            return messages

    def wait_for_parent_input(self, parent_id: str, token: CancellationToken) -> bool:
        with self._changed:
            if self._parent_inputs.get(parent_id):
                return True
            children = [row for row in self._children.values() if row.parent_id == parent_id]
            if not any(row.status == "running" for row in children):
                return bool(self._parent_inputs.get(parent_id))
            while not self._parent_inputs.get(parent_id):
                if token.cancelled or self._closed:
                    return False
                self._changed.wait(0.1)
            return True

    def send(self, sender_id: Optional[str], target_name: str, message: str, token: Optional[CancellationToken] = None) -> str:
        if token is not None:
            token.throw_if_cancelled()
        if not sender_id or not message.strip():
            raise SubagentError("sender and non-empty message are required", "INVALID_ARGUMENT")
        with self._changed:
            target = self._resolve(sender_id, target_name)
            sender = self._children.get(sender_id)
            root_id = sender.root_id if sender is not None else sender_id
            if target.child_id != root_id and target.root_id != root_id:
                raise SubagentError("target is outside the caller's subagent tree", "UNAUTHORIZED")
            if target.status == "running":
                target.inbox.append((uuid.uuid4().hex, self._display_name(sender_id), message))
                message_id = target.inbox[-1][0]
                self._changed.notify_all()
                return message_id
            if target.child_id == root_id:
                self._parent_inputs.setdefault(root_id, []).append({
                    "role": "user", "content": [text_block(message)],
                    "source": {"kind": "agent-message", "form": "relay", "senderSessionId": sender_id},
                })
                self._changed.notify_all()
                return uuid.uuid4().hex
            self._start_worker(target, message, self._display_name(sender_id))
            return target.inbox[-1][0]

    def interrupt(self, caller_id: Optional[str], target_name: str) -> bool:
        if not caller_id:
            raise SubagentError("interrupt_agent requires a calling agent", "CALLER_REQUIRED")
        with self._changed:
            target = self._resolve(caller_id, target_name)
            if target.child_id == target.root_id:
                raise SubagentError("cannot interrupt the root as a child", "UNAUTHORIZED")
            ancestor = target.parent_id
            caller = self._children.get(caller_id)
            root_id = caller.root_id if caller is not None else caller_id
            if target.root_id != root_id:
                raise SubagentError("target is outside the caller's tree", "UNAUTHORIZED")
            authorized = caller_id == target.root_id
            while ancestor != target.root_id:
                if ancestor == caller_id:
                    authorized = True
                    break
                parent = self._children.get(ancestor)
                if parent is None:
                    break
                ancestor = parent.parent_id
            if not authorized:
                raise SubagentError("only an ancestor can interrupt this subagent", "UNAUTHORIZED")
            target.token.cancel()
            self._changed.notify_all()
            return True

    def list_agents(self, caller_id: Optional[str]) -> List[Dict[str, object]]:
        if not caller_id:
            raise SubagentError("list_agents requires a calling agent", "CALLER_REQUIRED")
        with self._changed:
            caller = self._children.get(caller_id)
            root_id = caller.root_id if caller is not None else caller_id
            root_status = "running" if root_id in self._running_roots else "inactive"
            if caller is not None and caller.status == "running":
                root_status = "running"
            rows: List[Dict[str, object]] = [{"name": ROOT_NAME, "status": root_status, "description": "root agent"}]
            descendants = [row for row in self._children.values() if row.root_id == root_id]
            for row in sorted(descendants, key=lambda item: item.created_at):
                rows.append({"name": row.name, "status": row.status, "description": row.label})
            return rows

    def wait_for_input(self, caller_id: Optional[str], timeout_ms: int, token: CancellationToken) -> Dict[str, bool]:
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 10000 <= timeout_ms <= 3600000:
            raise SubagentError("timeout_ms must be an integer from 10000 through 3600000", "WAIT_INVALID_TIMEOUT")
        if not caller_id:
            raise SubagentError("wait_agent requires a calling agent", "WAIT_UNAVAILABLE")
        deadline = time.monotonic() + timeout_ms / 1000
        with self._changed:
            while True:
                caller = self._children.get(caller_id)
                if self._parent_inputs.get(caller_id) or (caller is not None and caller.inbox):
                    return {"timedOut": False}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"timedOut": True}
                token.throw_if_cancelled()
                self._changed.wait(min(remaining, 0.1))

    def active_children(self, parent_id: str) -> List[Dict[str, object]]:
        with self._lock:
            return [self._public_record(row) for row in self._children.values()
                    if row.parent_id == parent_id and row.status == "running"]

    def record(self, child_id: str) -> Dict[str, object]:
        with self._lock:
            return self._public_record(self._resolve(child_id, child_id))

    def _public_record(self, row: ChildRecord) -> Dict[str, object]:
        return {"id": row.child_id, "parentId": row.parent_id, "rootId": row.root_id, "name": row.name,
                "label": row.label, "provider": row.provider, "cwd": row.cwd, "depth": row.depth,
                "status": row.status, "stopReason": row.stop_reason, "output": row.output,
                "diagnostic": row.diagnostic}

    def _display_name(self, session_id: str) -> str:
        row = self._children.get(session_id)
        return row.name if row is not None else ROOT_NAME

    def _resolve(self, caller_id: str, name: str) -> ChildRecord:
        caller = self._children.get(caller_id)
        root_id = caller.root_id if caller is not None else caller_id
        if name == ROOT_NAME:
            return ChildRecord(root_id, "", root_id, ROOT_NAME, "root agent", "local", str(Path.cwd()), 0,
                               status="running" if root_id in self._running_roots else "inactive")
        if name in self._children:
            target = self._children[name]
        else:
            matches = [row for row in self._children.values() if row.name == name and row.root_id == root_id]
            if not matches:
                raise SubagentError(f"agent not found: {name}", "AGENT_NAME_NOT_FOUND")
            target = matches[0]
        if target.root_id != root_id:
            raise SubagentError("target is outside the caller's tree", "UNAUTHORIZED")
        return target

    def close(self) -> None:
        with self._changed:
            self._closed = True
            records = list(self._children.values())
            for record in records:
                record.token.cancel()
            self._changed.notify_all()
        for record in sorted(records, key=lambda item: item.depth, reverse=True):
            if record.thread is not None and record.thread is not threading.current_thread():
                record.thread.join(timeout=5)


class SubagentPlugin(Plugin):
    name = "core.subagents"
    provides = ("subagents",)
    requires = ("session",)

    def __init__(self) -> None:
        self.service: Optional[SubagentService] = None

    def activate(self, ctx: ServiceContext) -> None:
        session = ctx.require("session")
        assert isinstance(session, SessionStore)
        try:
            max_depth = int(os.environ.get("MINIDSH_SUBAGENT_MAX_DEPTH", "1"))
        except ValueError as exc:
            raise ValueError("MINIDSH_SUBAGENT_MAX_DEPTH must be a non-negative integer") from exc
        if max_depth < 0:
            raise ValueError("MINIDSH_SUBAGENT_MAX_DEPTH must be a non-negative integer")
        self.service = SubagentService(ctx, session, max_depth)
        ctx.provide("subagents", self.service, self.name)

    def deactivate(self, ctx: ServiceContext) -> None:
        if self.service is not None:
            self.service.close()


class SubagentToolPlugin(Plugin):
    requires = ("tools", "systemPrompt", "subagents")

    def __init__(self, name: str = "tools.subagent", provider: str = "spawn") -> None:
        self.name = name
        self.provider = provider
        self.tool_name = "subagent" if provider == "spawn" else "subagent_fork"

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        service = ctx.require("subagents")
        assert isinstance(registry, ToolRegistry) and isinstance(prompt, SystemPrompt)
        assert isinstance(service, SubagentService)
        fork = self.provider == "fork"
        wording = (
            "Delegate a task to a subagent that inherits this conversation: a child agent seeded with all "
            "completed turns so far (it does not see the current in-flight turn). You receive its result, not its intermediate steps."
            if fork else
            "Delegate a self-contained task to a subagent (a separate agent that works in its own context) "
            "to offload focused, independent work so it does not consume this conversation's context. "
            "The subagent returns its result, not its intermediate steps."
        )
        wording += (
            " This tool starts an independently managed subagent and immediately returns its name. If you are still "
            "online when it finishes, the runtime automatically delivers its completion notice. Use `send_message` "
            "to steer it while running or continue its conversation after it finishes."
        )

        def execute(arguments: Dict[str, object], execution: object) -> str:
            prompt_value = arguments.get("prompt")
            description = arguments.get("description")
            name = arguments.get("name")
            cwd = arguments.get("cwd")
            if not isinstance(prompt_value, str) or not isinstance(description, str):
                raise SubagentError("description and prompt must be strings", "INVALID_ARGUMENT")
            if name is not None and not isinstance(name, str):
                raise SubagentError("name must be a string", "INVALID_ARGUMENT")
            if cwd is not None and not isinstance(cwd, str):
                raise SubagentError("cwd must be a string", "INVALID_ARGUMENT")
            result = service.start(execution.session_id, prompt_value, description, self.provider, name, cwd,
                                   execution.cancel_token)
            return f"started subagent {result['name']}"

        tool = Tool(
            name=self.tool_name,
            description=wording,
            parameters={
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Initial child working directory. Relative paths use your current directory; omitted inherits it. Later directory changes in either agent are independent."},
                    "description": {"type": "string", "description": "A short (3-5 word) description of the delegated task, for display. Use the language of the user's current request."},
                    "name": {"type": "string", "description": "Optional short name for the subagent, unique among the subagents you create: lower-case letters, digits, and hyphens, starting with a letter, at most 32 characters; lead is reserved for the root agent."},
                    "prompt": {"type": "string", "description": "The task for the subagent. Forked children see completed turns; spawned children require a complete, self-contained task."},
                },
                "required": ["description", "prompt"],
                "additionalProperties": False,
            },
            handler=execute,
            kind="subagent",
        )
        registry.register(tool)
        prompt.register(f"tool:{self.tool_name}", 1700, SUBAGENT_GUIDANCE.replace("`subagent`", f"`{self.tool_name}`"))

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        assert isinstance(registry, ToolRegistry) and isinstance(prompt, SystemPrompt)
        registry.unregister(self.tool_name)
        prompt.unregister(f"tool:{self.tool_name}")


class SubagentControlPlugin(Plugin):
    name = "tools.subagent-control"
    requires = ("tools", "systemPrompt", "subagents")

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        service = ctx.require("subagents")
        assert isinstance(registry, ToolRegistry) and isinstance(prompt, SystemPrompt)
        assert isinstance(service, SubagentService)

        def send(arguments: Dict[str, object], execution: object) -> str:
            agent = arguments.get("agent")
            message = arguments.get("message")
            if not isinstance(agent, str) or not isinstance(message, str):
                raise SubagentError("agent and message must be strings", "INVALID_ARGUMENT")
            token = execution.cancel_token or CancellationToken()
            message_id = service.send(execution.session_id, agent, message, token)
            return json.dumps({"messageId": message_id}, ensure_ascii=False)

        def interrupt(arguments: Dict[str, object], execution: object) -> str:
            agent = arguments.get("agent")
            if not isinstance(agent, str):
                raise SubagentError("agent must be a string", "INVALID_ARGUMENT")
            service.interrupt(execution.session_id, agent)
            return json.dumps({"accepted": True})

        def list_agents(arguments: Dict[str, object], execution: object) -> str:
            rows = service.list_agents(execution.session_id)
            return "\n".join(f"{row['name']} [{row['status']}] — {row['description']}" for row in rows)

        def wait(arguments: Dict[str, object], execution: object) -> str:
            timeout = arguments.get("timeout_ms", 30000)
            result = service.wait_for_input(execution.session_id, timeout, execution.cancel_token or CancellationToken())
            return json.dumps(result)

        tools = [
            Tool("send_message", "Send a message to an agent. A working agent receives it at its next step; an idle agent starts a new turn with it. Returns delivery confirmation.",
                 {"type": "object", "properties": {"agent": {"type": "string"}, "message": {"type": "string"}},
                  "required": ["agent", "message"], "additionalProperties": False}, send, kind="subagent"),
            Tool("interrupt_agent", "Ask a subagent to stop its current work. This call returns without waiting for it to stop. You can continue a local subagent's conversation later with send_message. Subagents it started will keep running.",
                 {"type": "object", "properties": {"agent": {"type": "string"}}, "required": ["agent"], "additionalProperties": False},
                 interrupt, kind="subagent"),
            Tool("list_agents", "List your entire subagent tree: the root agent and all reachable continuable descendants, including yourself. Rows carry names, descriptions, and status. Use send_message to continue the conversation.",
                 {"type": "object", "properties": {}, "additionalProperties": False}, list_agents, kind="subagent"),
            Tool("wait_agent", "Wait for input at your next step, including agent messages, completion notifications, and user steering. Returns immediately if new input is pending.",
                 {"type": "object", "properties": {"timeout_ms": {"type": "integer", "description": "Wait duration in milliseconds, from 10000 through 3600000. Defaults to 30000."}}, "additionalProperties": False},
                 wait, kind="subagent"),
        ]
        self._tools = tools
        for tool in tools:
            registry.register(tool)
        prompt.register("tool:subagent-control", 1710, CONTROL_GUIDANCE)

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        assert isinstance(registry, ToolRegistry) and isinstance(prompt, SystemPrompt)
        for tool in self._tools:
            registry.unregister(tool.name)
        prompt.unregister("tool:subagent-control")
