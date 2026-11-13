import codecs
import fcntl
import os
import pty
import re
import signal
import subprocess
import termios
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .cancellation import CancellationToken, RunCancelled
from .events import EventBus
from .plugins import Plugin, ServiceContext
from .sandbox import SandboxPolicy
from .seatbelt import SeatbeltService
from .session import SessionStore
from .shell import scrub_environment
from .tools import Execution, Tool, ToolError, ToolRegistry


DEFAULT_PERSISTENT_TIMEOUT_MS = 300_000
DEFAULT_MAX_OUTPUT_CHARS = 16_000
SCROLLBACK_CHARS = 1024 * 1024
RESET_MESSAGE = "The persistent bash shell was reset; the next bash call starts from the workspace with a fresh current directory and environment."


def bash_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("\r", "\\r").replace("\n", "\\n")
    return f"$'{escaped}'"


@dataclass
class CommandResult:
    text: str
    exit_code: Optional[int] = None
    exited: bool = False
    timed_out: bool = False


class PtySession:
    def __init__(self, owner: str, workspace: Path, kernel_sandbox: Optional[SeatbeltService] = None, sandbox_mode: str = "read-only") -> None:
        self.id = uuid.uuid4().hex
        self.owner = owner
        self.workspace = workspace
        self.created_at = time.time()
        self.master, slave = pty.openpty()
        env = scrub_environment(owner)
        env.update({"TERM": "dumb", "PAGER": "cat", "GIT_PAGER": "cat", "DSH_SHELL": "1", "DSH_PTY_SESSION_ID": self.id, "PS1": ""})

        def controlling_terminal() -> None:
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        argv = kernel_sandbox.argv(sandbox_mode, interactive=True) if kernel_sandbox is not None else ["/bin/bash", "--noprofile", "--norc", "-i"]
        try:
            self.process = subprocess.Popen(
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=workspace,
                env=env,
                preexec_fn=controlling_terminal,
                close_fds=True,
            )
        except Exception:
            os.close(self.master)
            os.close(slave)
            raise
        os.close(slave)
        os.set_blocking(self.master, False)
        self.condition = threading.Condition()
        self.buffer = ""
        self.dropped = False
        self.reader = threading.Thread(target=self._read, name=f"minidsh-pty-{self.id[:8]}", daemon=True)
        self.reader.start()

    def _read(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            try:
                data = os.read(self.master, 65536)
            except BlockingIOError:
                if self.process.poll() is not None:
                    break
                time.sleep(0.01)
                continue
            except OSError:
                break
            if not data:
                break
            text = decoder.decode(data)
            with self.condition:
                self.buffer += text
                if len(self.buffer) > SCROLLBACK_CHARS:
                    self.buffer = self.buffer[-SCROLLBACK_CHARS:]
                    self.dropped = True
                self.condition.notify_all()
        with self.condition:
            self.process.wait()
            self.condition.notify_all()

    def execute(self, command: str, timeout_ms: int, cancel_token: Optional[CancellationToken]) -> CommandResult:
        nonce = uuid.uuid4().hex
        start = f"__MINIDSH_PERSISTENT_START_{nonce}__"
        end = f"__MINIDSH_PERSISTENT_END_{nonce}:"
        wrapped = (
            f"printf '%s\\n' {bash_quote(start)}; eval -- {bash_quote(command)}; "
            f"__minidsh_status=$?; printf '%s%s\\n' {bash_quote(end)} \"$__minidsh_status\""
        )
        deadline = time.monotonic() + timeout_ms / 1000

        def wake() -> None:
            with self.condition:
                self.condition.notify_all()

        unregister = cancel_token.on_cancel(wake) if cancel_token is not None else lambda: None
        try:
            with self.condition:
                if self.process.poll() is not None:
                    return CommandResult(self._partial(start, end), exited=True)
                os.write(self.master, wrapped.encode("utf-8") + b"\n")
                while True:
                    if cancel_token is not None:
                        cancel_token.throw_if_cancelled()
                    result = self._complete(start, end)
                    if result is not None:
                        return result
                    if self.process.poll() is not None:
                        return CommandResult(self._partial(start, end), exited=True)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return CommandResult(self._partial(start, end), timed_out=True)
                    self.condition.wait(min(remaining, 0.1))
        finally:
            unregister()

    def _complete(self, start: str, end: str) -> Optional[CommandResult]:
        end_at = self.buffer.rfind(end)
        if end_at < 0:
            return None
        match = re.match(r"(\d+)\r?\n", self.buffer[end_at + len(end):])
        if match is None:
            return None
        start_at = self.buffer.rfind(start, 0, end_at)
        text = self.buffer[start_at + len(start):end_at] if start_at >= 0 else self.buffer[:end_at]
        text = re.sub(r"^(?:\r?\n)", "", text)
        text = re.sub(r"(?:\r?\n)+$", "", text)
        return CommandResult(text, int(match.group(1)))

    def _partial(self, start: str, end: str) -> str:
        start_at = self.buffer.rfind(start)
        text = self.buffer[start_at + len(start):] if start_at >= 0 else self.buffer
        end_at = text.rfind(end)
        if end_at >= 0:
            text = text[:end_at]
        return re.sub(r"(?:\r?\n)+$", "", re.sub(r"^(?:\r?\n)", "", text))

    def status(self) -> str:
        return "exited" if self.process.poll() is not None else "running"

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                foreground = os.tcgetpgrp(self.master)
                if foreground > 0:
                    os.killpg(foreground, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=1)
        try:
            os.close(self.master)
        except OSError:
            pass
        with self.condition:
            self.condition.notify_all()


class PtyService:
    def __init__(
        self,
        workspace: Path,
        events: EventBus,
        kernel_sandbox: Optional[SeatbeltService] = None,
        sandbox_policy: Optional[SandboxPolicy] = None,
    ) -> None:
        self.workspace = workspace
        self.events = events
        self.kernel_sandbox = kernel_sandbox
        self.sandbox_policy = sandbox_policy
        self._sessions: Dict[str, PtySession] = {}
        self._owners: Dict[str, str] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def execute(
        self,
        owner: str,
        command: str,
        timeout_ms: int = DEFAULT_PERSISTENT_TIMEOUT_MS,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        cancel_token: Optional[CancellationToken] = None,
        session: Optional[SessionStore] = None,
    ) -> str:
        owner_lock = self._owner_lock(owner)
        while not owner_lock.acquire(timeout=0.05):
            if cancel_token is not None:
                cancel_token.throw_if_cancelled()
        try:
            shell = self._get(owner, cancel_token, session)
            try:
                result = shell.execute(command, timeout_ms, cancel_token)
            except RunCancelled:
                self._reset(shell, "persistent bash command aborted", session, cancel_token.run_id if cancel_token else None)
                raise
            output = self._clip(result.text, max_output_chars, shell.dropped)
            if result.timed_out:
                self._reset(shell, "persistent bash command timed out", session, cancel_token.run_id if cancel_token else None)
                return f"Command timed out after {round(timeout_ms / 1000)} seconds.\n{output}\n[Command timed out]\n{RESET_MESSAGE}"
            if result.exited:
                code = shell.process.returncode
                self._reset(shell, "persistent bash shell exited", session, cancel_token.run_id if cancel_token else None)
                return f"{output}\n[shell exited: code {code}]\n{RESET_MESSAGE}"
            marker = f"[Command finished with exit code {result.exit_code}]"
            return f"{output}\n{marker}" if output else marker
        finally:
            owner_lock.release()

    def _get(self, owner: str, token: Optional[CancellationToken], session: Optional[SessionStore]) -> PtySession:
        with self._lock:
            existing = self._sessions.get(self._owners.get(owner, ""))
            if existing is not None and existing.status() == "running":
                return existing
            if token is not None:
                token.throw_if_cancelled()
            mode = self.sandbox_policy.resolve(session, owner).mode if self.sandbox_policy is not None and session is not None else "read-only"
            shell = PtySession(owner, self.workspace, self.kernel_sandbox, mode)
            self._sessions[shell.id] = shell
            self._owners[owner] = shell.id
        try:
            result = shell.execute("stty -echo; set +H", 5_000, token)
            if result.exited or result.timed_out or result.exit_code != 0:
                raise RuntimeError("persistent bash shell did not accept initialization")
        except Exception:
            self._reset(shell, "persistent bash initialization failed", session, token.run_id if token else None)
            raise
        payload = {"ptyId": shell.id, "owner": owner, "run_id": token.run_id if token else None}
        if session is not None:
            session.append(owner, "pty/created", payload)
        self.events.emit("pty/created", payload)
        return shell

    def _owner_lock(self, owner: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(owner, threading.Lock())

    def _reset(self, shell: PtySession, reason: str, session: Optional[SessionStore], run_id: Optional[str]) -> None:
        with self._lock:
            self._sessions.pop(shell.id, None)
            if self._owners.get(shell.owner) == shell.id:
                del self._owners[shell.owner]
        shell.close()
        payload = {"ptyId": shell.id, "owner": shell.owner, "reason": reason, "run_id": run_id}
        if session is not None:
            session.append(shell.owner, "pty/reset", payload)
        self.events.emit("pty/reset", payload)

    def list(self) -> List[Dict[str, object]]:
        return [
            {"id": shell.id, "owner": shell.owner, "status": shell.status(), "createdAt": shell.created_at}
            for shell in list(self._sessions.values())
        ]

    def kill(self, terminal_id: str, reason: Optional[str] = None) -> bool:
        shell = self._sessions.get(terminal_id)
        if shell is None:
            return False
        self._reset(shell, reason or "terminal killed", None, None)
        return True

    def close(self) -> None:
        for shell in list(self._sessions.values()):
            self._reset(shell, "PTY service disposed", None, None)

    @staticmethod
    def _clip(text: str, limit: int, dropped: bool) -> str:
        clipped = len(text) > limit
        output = text[-limit:] if clipped else text
        if clipped or dropped:
            output = "<response clipped><NOTE>The beginning of this command output was dropped by the terminal scrollback limit.</NOTE>\n" + output
        return output


class PtyPlugin(Plugin):
    name = "core.pty"
    provides = ("terminals",)

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def activate(self, ctx: ServiceContext) -> None:
        kernel = ctx.optional("kernelSandbox")
        policy = ctx.optional("sandboxPolicy")
        assert kernel is None or isinstance(kernel, SeatbeltService)
        assert policy is None or isinstance(policy, SandboxPolicy)
        ctx.provide("terminals", PtyService(self.workspace, ctx.events, kernel, policy), self.name)

    def deactivate(self, ctx: ServiceContext) -> None:
        terminals = ctx.require("terminals")
        assert isinstance(terminals, PtyService)
        terminals.close()


class PersistentBashPlugin(Plugin):
    name = "tools.bash-persistent"
    requires = ("tools", "terminals")

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        terminals = ctx.require("terminals")
        assert isinstance(registry, ToolRegistry)
        assert isinstance(terminals, PtyService)
        try:
            timeout = int(os.environ.get("MINIDSH_PERSISTENT_TIMEOUT_MS", str(DEFAULT_PERSISTENT_TIMEOUT_MS)))
            max_output = int(os.environ.get("MINIDSH_PERSISTENT_MAX_OUTPUT_CHARS", str(DEFAULT_MAX_OUTPUT_CHARS)))
        except ValueError as exc:
            raise ValueError("持久 Shell 数值配置必须是整数") from exc
        if timeout <= 0 or max_output <= 0:
            raise ValueError("持久 Shell 超时和输出上限必须大于 0")

        def execute(arguments: Dict[str, object], execution: Execution) -> str:
            command = arguments.get("command")
            if not isinstance(command, str) or not command.strip():
                raise ToolError("command must be a non-empty string")
            if arguments.get("run_in_background") is True:
                raise ToolError("persistent bash does not support run_in_background")
            if execution.session_id is None:
                raise ToolError("persistent bash requires an owning session")
            return terminals.execute(
                execution.session_id,
                command,
                timeout,
                max_output,
                execution.cancel_token,
                execution.session(),
            )

        registry.register(
            Tool(
                name="bash",
                description="Run commands in a persistent bash shell. State, including the current directory and exported environment variables, persists across calls for this agent.",
                parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string", "description": "The bash command to run. Relative path is preferred in the command."}},
                    "required": ["command"],
                },
                handler=execute,
                mutating=True,
                kind="shell",
            )
        )

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        registry.unregister("bash")
