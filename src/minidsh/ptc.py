import json
import os
import queue
import shlex
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from .cancellation import CancellationToken
from .plugins import Plugin, ServiceContext
from .sandbox import SandboxPolicy
from .seatbelt import SeatbeltService
from .session import SessionStore
from .system_prompt import SystemPrompt
from .tools import Execution, Tool, ToolError, ToolRegistry


PTC_ONLY_GUIDANCE = "Only `run_code` is directly callable. The other declarations are SDK bindings available only inside the program, not separate tool schemas."
SDK_GUIDANCE = """## Writing code for run_code

`run_code` takes `code`, the body of an async TypeScript function or a Node REPL program, and `description`, a short summary of the work. In per-call mode, use `return` and/or `console.log(...)`. In persistent REPL mode, finish with a JSON expression; top-level return is not supported. Variables persist only in persistent mode.

Inside the program:
- Call tools as `await tools.name(args)` — quoted access for exotic names: `tools["my-tool"](args)`. Every call resolves to the tool's result value. Arguments must be lossless JSON.
- A failed tool call rejects with `ToolCallError`, whose `toolName` identifies the failed tool. Use try/catch to continue.
- Independent read-only calls MAY overlap under `Promise.all`; mutating calls run alone in submission order.
- Independent tool calls share a per-run count and concurrency cap. Exceeding either stops the program.
- Intermediate values stay inside the program; only printed and returned output is program output.

Program-only SDK bindings:
"""


class PtcError(ToolError):
    pass


class PtcService:
    def __init__(self, ctx: ServiceContext, session: SessionStore, workspace: Path) -> None:
        self.ctx = ctx
        self.session = session
        self.workspace = workspace
        self.default_timeout = self._integer("MINIDSH_PTC_TIMEOUT_MS", 120000)
        self.max_timeout = self._integer("MINIDSH_PTC_MAX_TIMEOUT_MS", 600000)
        self.max_output = self._integer("MINIDSH_PTC_MAX_OUTPUT_BYTES", 67108864)
        self.max_pending = self._integer("MINIDSH_PTC_MAX_PENDING", 128)
        self.stop_grace = self._integer("MINIDSH_PTC_STOP_GRACE_MS", 3000, allow_zero=True)
        self.max_subcalls = self._integer("MINIDSH_PTC_MAX_SUBCALLS_PER_MINUTE", 1024)
        self.max_parallel = self._integer("MINIDSH_PTC_MAX_PARALLEL", 10)
        self._workers: Dict[str, subprocess.Popen] = {}
        self._queues: Dict[str, queue.Queue] = {}
        self._active: Dict[str, bool] = {}
        self._closed = False
        self._lock = threading.RLock()

    @staticmethod
    def _integer(name: str, default: int, allow_zero: bool = False) -> int:
        try:
            value = int(os.environ.get(name, str(default)))
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if value < 0 if allow_zero else value < 1:
            raise ValueError(f"{name} has an invalid value")
        return value

    def model_definitions(self, definitions: List[Dict[str, object]]) -> List[Dict[str, object]]:
        return [dict(definition) for definition in definitions if definition.get("name") == "run_code"]

    def system_prompt(self, prompt: SystemPrompt) -> str:
        native = prompt.render_without_tools()
        registry = self.ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        declarations = "\n\n".join(json.dumps(definition, ensure_ascii=False) for definition in registry.definitions() if definition["name"] != "run_code")
        return "\n\n".join(part for part in (native, PTC_ONLY_GUIDANCE, SDK_GUIDANCE + declarations) if part)

    def execute(self, session_id: Optional[str], code: object, timeout_ms: object, token: Optional[CancellationToken], persistent: bool) -> Dict[str, object]:
        if not session_id:
            raise PtcError("run_code requires a calling session")
        if not isinstance(code, str) or not code.strip():
            raise PtcError("code must be a non-empty string")
        timeout = self.default_timeout if timeout_ms is None else timeout_ms
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise PtcError("timeoutMs must be a positive integer")
        timeout = min(timeout, self.max_timeout)
        with self._lock:
            if self._closed or self._active.get(session_id):
                raise PtcError("run_code is exclusive per session")
            self._active[session_id] = True
        execution = _Execution(self, session_id, code, timeout, persistent, token)
        try:
            return execution.run()
        finally:
            with self._lock:
                self._active.pop(session_id, None)

    def _spawn(self, session_id: str, persistent: bool) -> subprocess.Popen:
        node = shutil.which("node")
        if node is None:
            raise PtcError("run_code requires a Node.js executable")
        script = str(Path(__file__).with_name("ptc_runner.js"))
        sandbox = self.ctx.require("sandboxPolicy")
        assert isinstance(sandbox, SandboxPolicy)
        policy = sandbox.resolve(self.session, session_id)
        kernel = self.ctx.optional("kernelSandbox")
        assert kernel is None or isinstance(kernel, SeatbeltService)
        argv = kernel.argv(policy.mode, shlex.join([node, script])) if kernel is not None else [node, script]
        return subprocess.Popen(argv, cwd=str(self.workspace), env={}, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            workers = list(self._workers.values())
            self._workers.clear()
        for process in workers:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass


class _Execution:
    def __init__(self, service: PtcService, session_id: str, code: str, timeout: int, persistent: bool, token: Optional[CancellationToken]) -> None:
        self.service = service
        self.session_id = session_id
        self.code = code
        self.timeout = timeout
        self.persistent = persistent
        self.token = token or CancellationToken()
        self.process: Optional[subprocess.Popen] = None
        self.messages: queue.Queue = queue.Queue()
        self.errors: List[str] = []
        self.logs: List[str] = []
        self.log_bytes = 0
        self.active: Dict[int, Dict[str, object]] = {}
        self.pending: List[Dict[str, object]] = []
        self.subcalls = 0
        self.stop_status: Optional[str] = None
        self.stop_diagnostic: Optional[str] = None
        self.stop_started = 0.0
        self.result: Optional[Dict[str, object]] = None
        self._result_lock = threading.Lock()

    def run(self) -> Dict[str, object]:
        timer = threading.Timer(self.timeout / 1000, lambda: self._stop("timeout", f"execution timed out after {self.timeout}ms"))
        unregister = self.token.on_cancel(lambda: self._stop("abort", "execution aborted"))
        try:
            self.process = self.service._workers.get(self.session_id) if self.persistent else None
            new_worker = self.process is None or self.process.poll() is not None
            if new_worker:
                self.process = self.service._spawn(self.session_id, self.persistent)
                if self.persistent:
                    self.service._workers[self.session_id] = self.process
            self.messages = self.service._queues.setdefault(self.session_id, queue.Queue())
            if new_worker:
                threading.Thread(target=self._read_stdout, args=(self.process,), daemon=True).start()
                threading.Thread(target=self._read_stderr, args=(self.process,), daemon=True).start()
            self._write({"type": "init", "code": self.code, "mode": "vm-script" if self.persistent else "per-call", "persistent": self.persistent})
            timer.start()
            self._loop()
        except Exception as exc:
            if self.result is None:
                self.result = self._result("worker-exit", None, str(exc))
        finally:
            timer.cancel()
            unregister()
            self._cleanup()
        assert self.result is not None
        return self.result

    def _loop(self) -> None:
        while self.result is None:
            if self.stop_status is not None and time.monotonic() - self.stop_started >= self.service.stop_grace / 1000:
                self._kill()
            self._start_pending()
            try:
                message = self.messages.get(timeout=0.02)
            except queue.Empty:
                if self.process is not None and self.process.poll() is not None:
                    status = self.stop_status or "worker-exit"
                    diagnostic = self.stop_diagnostic or (self.errors[-1] if self.errors else "PTC worker exited")
                    self.result = self._result(status, None, diagnostic)
                continue
            kind = message.get("type")
            if kind == "log":
                text = str(message.get("text", ""))
                self.logs.append(text)
                self.log_bytes += len(text.encode())
                if self.log_bytes > self.service.max_output:
                    self._stop("output-limit", "PTC output limit exceeded")
            elif kind == "call":
                self.subcalls += 1
                quota = int(self.timeout / 60000 * self.service.max_subcalls)
                if self.subcalls > quota:
                    self._stop("abort", f"run_code exceeded its limit of {quota} tool calls; call {self.subcalls} was not dispatched")
                    continue
                self.pending.append({"id": message["id"], "name": message.get("name"), "args": message.get("args", {})})
                if len(self.pending) + len(self.active) > self.service.max_pending:
                    self._stop("abort", "too many pending PTC tool calls")
            elif kind == "tool-done":
                call_id = int(message["id"])
                self.active.pop(call_id, None)
                if self.stop_status is None:
                    response = {"type": "result", "id": call_id, "value": message.get("value")}
                    if message.get("error") is not None:
                        response = {"type": "result", "id": call_id, "error": {"toolName": message.get("name"), "message": message["error"]}}
                    self._write(response)
            elif kind == "done":
                status = self.stop_status or str(message.get("status", "exception"))
                value = message.get("value") if status == "completed" else None
                if status == "completed":
                    try:
                        output_bytes = self.log_bytes + len(json.dumps(value, ensure_ascii=False).encode())
                        if output_bytes > self.service.max_output:
                            status, value = "output-limit", None
                            diagnostic = "PTC output limit exceeded"
                        else:
                            diagnostic = None
                    except (TypeError, ValueError) as exc:
                        status, value, diagnostic = "invalid-output", None, str(exc)
                else:
                    diagnostic = self.stop_diagnostic or message.get("diagnostic")
                self.result = self._result(status, value, diagnostic)
            self._start_pending()

    def _start_pending(self) -> None:
        registry = self.service.ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        while self.pending and self.stop_status is None:
            mutating_active = any(call["mutating"] for call in self.active.values())
            if len(self.active) >= self.service.max_parallel or mutating_active:
                return
            call = self.pending[0]
            mutating = registry.mutating(str(call["name"]))
            if mutating and self.active:
                return
            self.pending.pop(0)
            call["mutating"] = mutating
            call_id = int(call["id"])
            self.active[call_id] = call
            threading.Thread(target=self._run_tool, args=(call,), daemon=True).start()

    def _run_tool(self, call: Dict[str, object]) -> None:
        registry = self.service.ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        call_id = int(call["id"])
        try:
            value = registry.execute(str(call["name"]), dict(call["args"]), self.session_id, f"{self.session_id}:ptc:{call_id}", cancel_token=self.token)
            message = {"type": "tool-done", "id": call_id, "value": value}
        except Exception as exc:
            message = {"type": "tool-done", "id": call_id, "name": call["name"], "error": str(exc)}
        self.messages.put(message)

    def _write(self, value: Dict[str, object]) -> None:
        if self.process is None or self.process.stdin is None or self.process.poll() is not None:
            raise PtcError("PTC control channel is closed")
        try:
            self.process.stdin.write((json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode())
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise PtcError(f"PTC control channel failed: {exc}") from exc

    def _read_stdout(self, process: subprocess.Popen) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                if len(line) > 134217728:
                    self.messages.put({"type": "done", "status": "protocol", "diagnostic": "PTC control message too large"})
                    return
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    self.messages.put({"type": "done", "status": "protocol", "diagnostic": "invalid PTC control message"})
                    continue
                if isinstance(value, dict):
                    self.messages.put(value)
        except OSError:
            pass

    def _read_stderr(self, process: subprocess.Popen) -> None:
        assert process.stderr is not None
        try:
            data = process.stderr.read(65537)
            if data:
                self.errors.append(data[:65536].decode("utf-8", errors="replace"))
        except OSError:
            pass

    def _stop(self, status: str, diagnostic: str) -> None:
        if self.stop_status is not None or self.result is not None:
            return
        self.stop_status = status
        self.stop_diagnostic = diagnostic
        self.stop_started = time.monotonic()
        self.pending.clear()
        try:
            self._write({"type": "revoke"})
        except Exception:
            pass

    def _kill(self) -> None:
        if self.process is not None and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _cleanup(self) -> None:
        process = self.process
        if process is None:
            return
        preserve = self.persistent and self.result is not None and self.result["status"] in {"completed", "exception"}
        if not preserve:
            self._kill()
            if self.service._workers.get(self.session_id) is process:
                self.service._workers.pop(self.session_id, None)
        if not preserve:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if self.result is not None:
                    self.result["status"] = "worker-exit"
                    self.result["diagnostic"] = "PTC worker did not exit during cleanup"
        if not preserve:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def _result(self, status: str, value: object, diagnostic: Optional[str]) -> Dict[str, object]:
        return {"status": status, "value": value, "logs": list(self.logs), "diagnostic": diagnostic}


class PtcPlugin(Plugin):
    name = "core.ptc"
    provides = ("ptc",)
    requires = ("session", "sandboxPolicy")

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.service: Optional[PtcService] = None

    def activate(self, ctx: ServiceContext) -> None:
        session = ctx.require("session")
        assert isinstance(session, SessionStore)
        self.service = PtcService(ctx, session, self.workspace)
        ctx.provide("ptc", self.service, self.name)

    def deactivate(self, ctx: ServiceContext) -> None:
        if self.service is not None:
            self.service.close()


class RunCodePlugin(Plugin):
    name = "tools.run-code"
    requires = ("tools", "ptc")

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        service = ctx.require("ptc")
        assert isinstance(registry, ToolRegistry) and isinstance(service, PtcService)
        persistent_default = os.environ.get("MINIDSH_PTC_PERSISTENT", "0") == "1"
        persistent = persistent_default
        def execute(arguments: Dict[str, object], execution: Execution) -> str:
            nonlocal persistent
            persistent = bool(arguments.get("persistent", persistent_default))
            timeout = arguments.get("timeoutMs")
            result = service.execute(execution.session_id, arguments.get("code"), timeout, execution.cancel_token, persistent)
            rendered = {"status": result["status"], "value": result["value"], "logs": result["logs"], "diagnostic": result["diagnostic"]}
            return json.dumps(rendered, ensure_ascii=False)
        schema = {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Async TypeScript function body, or Node REPL code in persistent mode."},
                "description": {"type": "string", "description": "Short summary of what the program does."},
                "timeoutMs": {"type": "integer", "description": f"Execution timeout in milliseconds; defaults to {service.default_timeout}, capped at {service.max_timeout}."},
                "persistent": {"type": "boolean", "description": "Use the session's persistent Node REPL environment."},
            },
            "required": ["code", "description"],
            "additionalProperties": False,
        }
        registry.register(Tool("run_code", "Run TypeScript or Node REPL code with program-only SDK bindings for the available tools.", schema, execute, kind="ptc"))

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        registry.unregister("run_code")
