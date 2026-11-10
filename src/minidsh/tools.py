import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .events import EventBus
from .plugins import Plugin, ServiceContext
from .system_prompt import GLOB_GUIDANCE, READ_GUIDANCE, SystemPrompt


READ_LIMIT = 2000
READ_MAX_LINE_LENGTH = 2000
READ_MAX_BYTES = 50 * 1024
GLOB_MAX_RESULTS = 100
VCS_EXCLUDES = {".git", ".svn", ".hg", ".bzr", ".jj", ".sl"}


class ToolError(ValueError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, object]
    handler: Callable[[Dict[str, object]], str]

    def definition(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    def __init__(self, events: EventBus) -> None:
        self._tools: Dict[str, Tool] = {}
        self._events = events

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具已注册: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def definitions(self) -> List[Dict[str, object]]:
        return [tool.definition() for tool in self._tools.values()]

    def execute(self, name: str, arguments: Dict[str, object]) -> str:
        self._events.emit("tools/pre-execute", {"name": name, "arguments": arguments})
        if name not in self._tools:
            raise ToolError(f"工具未启用或不存在: {name}")
        try:
            result = self._tools[name].handler(arguments)
        except Exception as exc:
            self._events.emit("tools/post-execute", {"name": name, "error": str(exc)})
            raise
        self._events.emit("tools/post-execute", {"name": name, "result": result})
        return result


class CoreToolsPlugin(Plugin):
    name = "core.tools"
    provides = ("tools",)

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("tools", ToolRegistry(ctx.events), self.name)


class SingleToolPlugin(Plugin):
    requires = ("tools", "systemPrompt")

    def __init__(
        self,
        name: str,
        tool: Tool,
        prompt_section: Tuple[str, int, str],
    ) -> None:
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


def workspace_path(workspace: Path, value: object, field: str = "path") -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{field} 必须是非空字符串")
    candidate = (workspace / value).expanduser().resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError as exc:
        raise ToolError("路径不能离开当前工作区") from exc
    return candidate


def positive_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolError(f"{name} must be a positive integer")
    return value


def truncate_line(line: str) -> str:
    if len(line) <= READ_MAX_LINE_LENGTH:
        return line
    return f"{line[:READ_MAX_LINE_LENGTH]}... (line truncated to {READ_MAX_LINE_LENGTH} chars)"


def make_read_tool(workspace: Path) -> Tool:
    def read(arguments: Dict[str, object]) -> str:
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
        return f"<path>{path}</path>\n<type>file</type>\n<content>\n{body}\n</content>"

    return Tool(
        name="read",
        description="Read a UTF-8 text file and return line-numbered content.",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to read, resolved by the filesystem backend.",
                },
                "offset": {
                    "type": "number",
                    "description": "1-based first line to return. Defaults to 1.",
                },
                "limit": {
                    "type": "number",
                    "description": f"Maximum number of lines to return. Defaults to {READ_LIMIT}.",
                },
            },
            "required": ["file_path"],
        },
        handler=read,
    )


def make_glob_tool(workspace: Path) -> Tool:
    def glob(arguments: Dict[str, object]) -> str:
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
