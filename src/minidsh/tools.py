import fnmatch
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .approval import ApprovalService
from .events import EventBus
from .observations import ObservationStore
from .plugins import Plugin, ServiceContext
from .sandbox import SandboxPolicy
from .session import SessionStore
from .system_prompt import SystemPrompt


READ_LIMIT = 2000
READ_MAX_LINE_LENGTH = 2000
READ_MAX_BYTES = 50 * 1024
GLOB_MAX_RESULTS = 100
VCS_EXCLUDES = {".git", ".svn", ".hg", ".bzr", ".jj", ".sl"}


class ToolError(ValueError):
    pass


@dataclass
class Execution:
    ctx: ServiceContext
    session_id: Optional[str]
    call_id: Optional[str]

    def service(self, name: str) -> object:
        return self.ctx.require(name)

    def session(self) -> Optional[SessionStore]:
        session = self.ctx.optional("session")
        assert session is None or isinstance(session, SessionStore)
        return session


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, object]
    handler: Callable[[Dict[str, object], Execution], str]
    mutating: bool = False
    kind: str = "filesystem"

    def definition(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    def __init__(self, ctx: ServiceContext) -> None:
        self._tools: Dict[str, Tool] = {}
        self._events = ctx.events
        self._ctx = ctx

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具已注册: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def definitions(self) -> List[Dict[str, object]]:
        return [tool.definition() for tool in self._tools.values()]

    def execute(
        self,
        name: str,
        arguments: Dict[str, object],
        session_id: Optional[str] = None,
        call_id: Optional[str] = None,
    ) -> str:
        self._events.emit("tools/pre-execute", {"name": name, "arguments": arguments})
        if name not in self._tools:
            raise ToolError(f"工具未启用或不存在: {name}")
        tool = self._tools[name]
        execution = Execution(self._ctx, session_id, call_id)
        try:
            if tool.mutating:
                self._authorize(execution, tool.name, tool.kind)
            result = tool.handler(arguments, execution)
        except Exception as exc:
            self._events.emit("tools/post-execute", {"name": name, "error": str(exc)})
            raise
        self._events.emit("tools/post-execute", {"name": name, "result": result})
        return result

    def _authorize(self, execution: Execution, tool_name: str, kind: str) -> None:
        sandbox = execution.service("sandboxPolicy")
        assert isinstance(sandbox, SandboxPolicy)
        if sandbox.mode == "read-only":
            action = "Shell 命令" if kind == "shell" else "写操作"
            raise ToolError(f"[sandbox] 当前文件策略为 read-only，{action}被拒绝")
        if sandbox.mode == "danger-full-access":
            return
        approval = execution.service("approval")
        assert isinstance(approval, ApprovalService)
        outcome = approval.request(tool_name, execution.call_id, execution.session_id, execution.session())
        if outcome != "allowed-once":
            raise ToolError(f"[sandbox] 操作审批结果: {outcome}")


class CoreToolsPlugin(Plugin):
    name = "core.tools"
    provides = ("tools",)

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("tools", ToolRegistry(ctx), self.name)


class SingleToolPlugin(Plugin):
    requires = ("tools", "systemPrompt")

    def __init__(self, name: str, tool: Tool, prompt_section: Tuple[str, int, str]) -> None:
        self.name = name
        self.tool = tool
        self.prompt_section = prompt_section

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        assert isinstance(registry, ToolRegistry)
        assert isinstance(prompt, SystemPrompt)
        registry.register(self.tool)
        prompt.register(*self.prompt_section)

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        assert isinstance(registry, ToolRegistry)
        assert isinstance(prompt, SystemPrompt)
        registry.unregister(self.tool.name)
        prompt.unregister(self.prompt_section[0])


def workspace_path(workspace: Path, value: object, field: str = "path", allow_outside: bool = False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{field} 必须是非空字符串")
    candidate = (workspace / value).expanduser().resolve()
    if not allow_outside:
        try:
            candidate.relative_to(workspace)
        except ValueError as exc:
            raise ToolError("路径不能离开当前工作区") from exc
    return candidate


def mutation_path(execution: Execution, workspace: Path, value: object) -> Path:
    sandbox = execution.service("sandboxPolicy")
    assert isinstance(sandbox, SandboxPolicy)
    return workspace_path(workspace, value, "file_path", allow_outside=sandbox.mode == "danger-full-access")


def observations(execution: Execution) -> ObservationStore:
    store = execution.service("observations")
    assert isinstance(store, ObservationStore)
    return store


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.minidsh.tmp"
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def positive_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolError(f"{name} must be a positive integer")
    return value


def truncate_line(line: str) -> str:
    if len(line) <= READ_MAX_LINE_LENGTH:
        return line
    return f"{line[:READ_MAX_LINE_LENGTH]}... (line truncated to {READ_MAX_LINE_LENGTH} chars)"


def make_read_tool(workspace: Path) -> Tool:
    def read(arguments: Dict[str, object], execution: Execution) -> str:
        file_path = arguments.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            raise ToolError("file_path must be a non-empty string")
        offset = positive_integer(arguments.get("offset", 1), "offset")
        limit = positive_integer(arguments.get("limit", READ_LIMIT), "limit")
        if limit > READ_LIMIT:
            raise ToolError(f"limit must be less than or equal to {READ_LIMIT}")
        path = workspace_path(workspace, file_path, "file_path")
        if not path.is_file():
            raise ToolError(f"不是文件: {path}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("read 只读取 UTF-8 文本") from exc

        all_lines = text.splitlines()
        total_lines = len(all_lines)
        if offset > total_lines and not (total_lines == 0 and offset == 1):
            raise ToolError(f'offset {offset} is out of range for "{file_path}" ({total_lines} lines)')

        lines: List[str] = []
        output_bytes = 0
        truncated_by_bytes = False
        for number, raw_line in enumerate(all_lines, start=1):
            if number < offset or len(lines) >= limit:
                continue
            rendered = truncate_line(raw_line)
            size = len(rendered.encode("utf-8")) + (1 if lines else 0)
            if output_bytes + size > READ_MAX_BYTES:
                truncated_by_bytes = True
                break
            output_bytes += size
            lines.append(f"{number}: {rendered}")

        end_line = offset + len(lines) - 1 if lines else max(0, offset - 1)
        if truncated_by_bytes:
            footer = f"(Output capped. Showing lines {offset}-{end_line}. Use offset={end_line + 1} to continue.)"
        elif end_line < total_lines:
            footer = f"(Showing lines {offset}-{end_line} of {total_lines}. Use offset={end_line + 1} to continue.)"
        else:
            footer = f"(End of file - total {total_lines} lines)"
        numbered = "\n".join(lines)
        body = f"{numbered}\n\n{footer}" if lines else footer
        observations(execution).record(execution.session_id, path, execution.session())
        return f"<path>{path}</path>\n<type>file</type>\n<content>\n{body}\n</content>"

    return Tool(
        name="read",
        description="Read a UTF-8 text file and return line-numbered content.",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to read, resolved by the filesystem backend."},
                "offset": {"type": "number", "description": "1-based first line to return. Defaults to 1."},
                "limit": {"type": "number", "description": f"Maximum number of lines to return. Defaults to {READ_LIMIT}."},
            },
            "required": ["file_path"],
        },
        handler=read,
    )


def make_glob_tool(workspace: Path) -> Tool:
    def glob(arguments: Dict[str, object], execution: Execution) -> str:
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ToolError("pattern must be a non-empty string")
        root = workspace_path(workspace, arguments.get("path", "."))
        if not root.is_dir():
            raise ToolError(f"不是目录: {root}")

        matches: List[Path] = []
        for current, directories, files in os.walk(root):
            directories[:] = [name for name in directories if name not in VCS_EXCLUDES]
            for name in files:
                path = Path(current) / name
                relative = path.relative_to(root).as_posix()
                if ("/" in pattern and fnmatch.fnmatchcase(relative, pattern)) or (
                    "/" not in pattern and fnmatch.fnmatchcase(name, pattern)
                ):
                    matches.append(path)
        matches.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        paths = [path.relative_to(workspace).as_posix() for path in matches]
        if not paths:
            return "No files found"
        if len(paths) <= GLOB_MAX_RESULTS:
            return "\n".join(paths)
        shown = "\n".join(paths[:GLOB_MAX_RESULTS])
        return (
            f"{shown}\n\n(Showing {GLOB_MAX_RESULTS} of {len(paths)} paths. "
            "The complete result could not be saved; narrow pattern or path to see more.)"
        )

    return Tool(
        name="glob",
        description=(
            "Find files, not directories, whose paths match a glob pattern, including hidden and ignored files. "
            f"Returns up to {GLOB_MAX_RESULTS} paths in modification-time order; a larger result keeps the first paths "
            "and reports where the complete list was saved."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Glob pattern to match file paths against (e.g. \"**/*.ts\", \"src/**/*.test.js\"). "
                        "A pattern with no \"/\" matches the basename at any depth, so \"*\" and \"*.ts\" both search "
                        "the whole tree; include a separator to anchor the depth."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. Defaults to the session workspace; a relative path resolves against it.",
                },
            },
            "required": ["pattern"],
        },
        handler=glob,
    )


def make_write_tool(workspace: Path) -> Tool:
    def write(arguments: Dict[str, object], execution: Execution) -> str:
        file_path = arguments.get("file_path")
        content = arguments.get("content")
        if not isinstance(file_path, str) or not file_path.strip():
            raise ToolError("file_path must be a non-empty string")
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        path = mutation_path(execution, workspace, file_path)
        operation = "update" if path.exists() else "create"
        if operation == "update":
            if not path.is_file():
                raise ToolError(f"不是文件: {path}")
            try:
                observations(execution).require_current(execution.session_id, path)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
        atomic_write(path, content)
        observations(execution).record(execution.session_id, path, execution.session())
        verb = "Created" if operation == "create" else "Updated"
        return f"<path>{path}</path>\n<type>file</type>\n<content>\n{verb} file\n</content>"

    return Tool(
        name="write",
        description="Create or fully replace a UTF-8 text file.",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to write, resolved by the filesystem backend. Provide `file_path` before `content` in the arguments.",
                },
                "content": {"type": "string", "description": "Full UTF-8 text content to write."},
            },
            "required": ["file_path", "content"],
        },
        handler=write,
        mutating=True,
    )


def make_edit_tool(workspace: Path) -> Tool:
    def edit(arguments: Dict[str, object], execution: Execution) -> str:
        file_path = arguments.get("file_path")
        old_string = arguments.get("old_string")
        new_string = arguments.get("new_string")
        replace_all = arguments.get("replace_all", False)
        if not isinstance(file_path, str) or not file_path.strip():
            raise ToolError("file_path must be a non-empty string")
        if not isinstance(old_string, str) or not old_string:
            raise ToolError("old_string must be a non-empty string")
        if not isinstance(new_string, str):
            raise ToolError("new_string must be a string")
        if old_string == new_string:
            raise ToolError("old_string and new_string must differ")
        if not isinstance(replace_all, bool):
            raise ToolError("replace_all must be a boolean")

        path = mutation_path(execution, workspace, file_path)
        if not path.is_file():
            raise ToolError(f"不是文件: {path}")
        try:
            observations(execution).require_current(execution.session_id, path)
            text = path.read_text(encoding="utf-8")
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except UnicodeDecodeError as exc:
            raise ToolError("edit 只读取 UTF-8 文本") from exc

        occurrences = text.count(old_string)
        if occurrences == 0:
            raise ToolError("old_string was not found in the file")
        if not replace_all and occurrences != 1:
            raise ToolError(f"old_string must appear exactly once; found {occurrences} occurrences")
        updated = text.replace(old_string, new_string, -1 if replace_all else 1)
        atomic_write(path, updated)
        observations(execution).record(execution.session_id, path, execution.session())
        if replace_all:
            return f"The file {path} has been updated. All occurrences were successfully replaced."
        return f"The file {path} has been updated successfully."

    return Tool(
        name="edit",
        description="Edit an existing UTF-8 text file by replacing literal text.",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to edit, resolved by the filesystem backend. Provide `file_path` before `old_string` and `new_string` in the arguments.",
                },
                "old_string": {"type": "string", "description": "Literal text to replace."},
                "new_string": {
                    "type": "string",
                    "description": "Literal replacement text. Use an empty string to delete the match.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all matches. Defaults to false; when false, old_string must appear exactly once.",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
        handler=edit,
        mutating=True,
    )
