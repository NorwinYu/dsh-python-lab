import os
from typing import Dict, List

from .plugins import Plugin, ServiceContext
from .tools import Execution, Tool, ToolError, ToolRegistry


STATUSES = {"pending", "in_progress", "completed"}


def canonical_todos(value: object, allow_parallel: bool) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        raise ToolError("todos 必须是完整任务数组")
    todos: List[Dict[str, str]] = []
    seen = set()
    active = 0
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {"content", "status"}:
            raise ToolError("每个 todo 只能包含 content 和 status")
        content = raw.get("content")
        status = raw.get("status")
        if not isinstance(content, str) or not content.strip():
            raise ToolError("todo content 必须是非空字符串")
        content = content.strip()
        if content in seen:
            raise ToolError(f"todo 内容不能重复: {content}")
        seen.add(content)
        if status not in STATUSES:
            raise ToolError("todo status 必须是 pending、in_progress 或 completed")
        if status == "in_progress":
            active += 1
        todos.append({"content": content, "status": status})
    if not allow_parallel and active > 1:
        raise ToolError(f"最多只能有一个 in_progress 任务，当前为 {active}")
    return todos


def allow_parallel_from_env() -> bool:
    return os.environ.get("MINIDSH_TODO_ALLOW_PARALLEL", "0").lower() in {"1", "true", "yes"}


class TodoPlugin(Plugin):
    name = "tools.todo"
    requires = ("tools", "session")

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        allow_parallel = allow_parallel_from_env()
        active_guidance = (
            "While work remains, keep the todos being worked on `in_progress`, several only when work runs in parallel. "
            if allow_parallel
            else "While work remains, keep exactly one todo `in_progress`. "
        )
        description = (
            "Record and update a task list to plan multi-step work and show progress; skip it for trivial single-step tasks. "
            "Add one todo per concrete step before you start. "
            + active_guidance
            + "Mark each todo `completed` as soon as it is done."
        )

        def write(arguments: Dict[str, object], execution: Execution) -> str:
            todos = canonical_todos(arguments.get("todos"), allow_parallel)
            session = execution.session()
            if session is None or execution.session_id is None:
                raise ToolError("todo_write requires an owning agent session")
            session.append(execution.session_id, "todo/write", {"todos": todos})
            counts = {status: sum(1 for todo in todos if todo["status"] == status) for status in STATUSES}
            return f"Updated todo list: {counts['pending']} pending, {counts['in_progress']} in progress, {counts['completed']} completed."

        registry.register(
            Tool(
                name="todo_write",
                description=description,
                parameters={
                    "type": "object",
                    "properties": {
                        "todos": {
                            "type": "array",
                            "description": "The COMPLETE task list, replacing any previous list.",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "content": {"type": "string", "description": "A short imperative task line."},
                                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                                },
                                "required": ["content", "status"],
                            },
                        }
                    },
                    "required": ["todos"],
                },
                handler=write,
            )
        )

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        registry.unregister("todo_write")
