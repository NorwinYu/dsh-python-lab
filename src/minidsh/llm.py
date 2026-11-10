from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List

from .plugins import Plugin, ServiceContext


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, object]
    call_id: str


@dataclass
class ModelResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)


class Model(ABC):
    @abstractmethod
    def complete(
        self,
        messages: List[Dict[str, object]],
        tool_definitions: List[Dict[str, object]],
    ) -> ModelResponse:
        pass


class MockModel(Model):
    def complete(
        self,
        messages: List[Dict[str, object]],
        tool_definitions: List[Dict[str, object]],
    ) -> ModelResponse:
        if messages[-1]["role"] == "tool":
            return ModelResponse(content=f"工具已经执行完成：\n{messages[-1]['content']}")

        prompt = next(
            (str(message["content"]) for message in reversed(messages) if message["role"] == "user"),
            "",
        ).strip()
        available = {definition["name"] for definition in tool_definitions}

        if ("目录" in prompt or prompt.lower().startswith("list")) and "list_dir" in available:
            return ModelResponse(
                content="我先查看目录。",
                tool_calls=[ToolCall(name="list_dir", arguments={"path": "."}, call_id="mock-1")],
            )

        if (prompt.startswith("读取") or prompt.lower().startswith("read ")) and "read_file" in available:
            path = prompt[2:].strip() if prompt.startswith("读取") else prompt[5:].strip()
            return ModelResponse(
                content="我先读取文件。",
                tool_calls=[ToolCall(name="read_file", arguments={"path": path}, call_id="mock-1")],
            )

        return ModelResponse(
            content=(
                "MockModel 已收到你的消息。当前可尝试：\n"
                "- 请列出当前目录\n"
                "- 读取 README.md\n"
                "也可以禁用某个工具，观察工具目录和插件状态如何变化。"
            )
        )


class MockModelPlugin(Plugin):
    name = "llm.mock"
    provides = ("llm",)

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("llm", MockModel(), self.name)
