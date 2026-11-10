import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List

from .events import EventBus
from .plugins import Plugin, ServiceContext


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
    requires = ("tools",)

    def __init__(self, name: str, tool: Tool) -> None:
        self.name = name
        self.tool = tool

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        registry.register(self.tool)

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        registry.unregister(self.tool.name)


def workspace_path(workspace: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ToolError("path 必须是非空字符串")
    candidate = (workspace / value).expanduser().resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError as exc:
        raise ToolError("路径不能离开当前工作区") from exc
    return candidate


def make_list_dir_tool(workspace: Path) -> Tool:
    def list_dir(arguments: Dict[str, object]) -> str:
        directory = workspace_path(workspace, arguments.get("path", "."))
        if not directory.is_dir():
            raise ToolError(f"不是目录: {directory}")
        entries = sorted(directory.iterdir(), key=lambda path: path.name)
        lines = [f"{entry.name}{'/' if entry.is_dir() else ''}" for entry in entries[:200]]
        if len(entries) > 200:
            lines.append(f"... 省略 {len(entries) - 200} 项")
        return "\n".join(lines) or "（空目录）"

    return Tool(
        name="list_dir",
        description="列出工作区内某个目录的内容",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
        },
        handler=list_dir,
    )


def make_read_file_tool(workspace: Path) -> Tool:
    def read_file(arguments: Dict[str, object]) -> str:
        path = workspace_path(workspace, arguments.get("path"))
        if not path.is_file():
            raise ToolError(f"不是文件: {path}")
        data = path.read_bytes()
        if len(data) > 64 * 1024:
            raise ToolError("文件超过 64 KiB 的学习版读取上限")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("学习版只读取 UTF-8 文本") from exc

    return Tool(
        name="read_file",
        description="读取工作区内不超过 64 KiB 的 UTF-8 文本文件",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=read_file,
    )


def arguments_json(arguments: Dict[str, object]) -> str:
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)
