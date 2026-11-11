from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .plugins import Plugin, ServiceContext


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, object]
    call_id: str


@dataclass
class ModelResponse:
    blocks: List[Dict[str, object]] = field(default_factory=list)
    finish_reason: Dict[str, object] = field(default_factory=lambda: {"kind": "stop"})
    usage: Optional[Dict[str, int]] = None
    replay_state: Optional[Dict[str, object]] = None

    @property
    def content(self) -> str:
        return "".join(str(block.get("text", "")) for block in self.blocks if block.get("type") == "text")

    @property
    def tool_calls(self) -> List[ToolCall]:
        calls: List[ToolCall] = []
        for block in self.blocks:
            if block.get("type") != "tool-call":
                continue
            arguments = block.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ValueError("工具参数必须是对象")
            calls.append(ToolCall(name=str(block["name"]), arguments=arguments, call_id=str(block["id"])))
        return calls


class Model(ABC):
    @abstractmethod
    def complete(
        self,
        system: str,
        messages: List[Dict[str, object]],
        tool_definitions: List[Dict[str, object]],
        session_id: Optional[str] = None,
    ) -> ModelResponse:
        pass


def text_block(text: str) -> Dict[str, object]:
    return {"type": "text", "text": text}


def tool_call_block(call: ToolCall) -> Dict[str, object]:
    return {"type": "tool-call", "id": call.call_id, "name": call.name, "arguments": call.arguments}


def edit_arguments(prompt: str) -> Dict[str, object]:
    request = prompt[2:].strip()
    path, operation = request.split("：把", 1)
    old_string, new_string = operation.split("替换为", 1)
    return {"file_path": path, "old_string": old_string, "new_string": new_string}


class MockModel(Model):
    provider = "mock"
    model_id = "mock"

    def complete(
        self,
        system: str,
        messages: List[Dict[str, object]],
        tool_definitions: List[Dict[str, object]],
        session_id: Optional[str] = None,
    ) -> ModelResponse:
        prompt = ""
        for message in reversed(messages):
            if message["role"] != "user":
                continue
            source = message.get("source", {})
            if isinstance(source, dict) and source.get("kind") == "runtime-context":
                continue
            content = message["content"]
            if isinstance(content, list):
                prompt = "".join(str(block.get("text", "")) for block in content if isinstance(block, dict)).strip()
            else:
                prompt = str(content).strip()
            if prompt:
                break

        available = {definition["name"] for definition in tool_definitions}
        if messages[-1]["role"] == "tool":
            last = messages[-1]
            if prompt.startswith("编辑") and last.get("name") == "read" and not last.get("isError"):
                call = ToolCall(name="edit", arguments=edit_arguments(prompt), call_id="mock-2")
                return ModelResponse(blocks=[text_block("读取完成，现在执行编辑。"), tool_call_block(call)])
            content = last["content"]
            if isinstance(content, list):
                text = "".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
            else:
                text = str(content)
            return ModelResponse(blocks=[text_block(f"工具已经执行完成：\n{text}")])

        if prompt.startswith("写入") and "write" in available:
            request = prompt[2:].strip()
            separator = "：" if "：" in request else ":"
            path, content = request.split(separator, 1) if separator in request else (request, "")
            call = ToolCall(name="write", arguments={"file_path": path, "content": content}, call_id="mock-1")
            return ModelResponse(blocks=[text_block("我准备写入文件，请先完成审批。"), tool_call_block(call)])

        if prompt.startswith("编辑") and "read" in available:
            call = ToolCall(name="read", arguments={"file_path": edit_arguments(prompt)["file_path"]}, call_id="mock-1")
            return ModelResponse(blocks=[text_block("编辑前我先读取文件。"), tool_call_block(call)])

        if ("目录" in prompt or prompt.lower().startswith("list")) and "glob" in available:
            call = ToolCall(name="glob", arguments={"pattern": "*"}, call_id="mock-1")
            return ModelResponse(blocks=[text_block("我先查看目录。"), tool_call_block(call)])

        if (prompt.startswith("读取") or prompt.lower().startswith("read ")) and "read" in available:
            path = prompt[2:].strip() if prompt.startswith("读取") else prompt[5:].strip()
            call = ToolCall(name="read", arguments={"file_path": path}, call_id="mock-1")
            return ModelResponse(blocks=[text_block("我先读取文件。"), tool_call_block(call)])

        return ModelResponse(
            blocks=[
                text_block(
                    "MockModel 已收到你的消息。当前可尝试：\n"
                    "- 请列出当前目录\n"
                    "- 读取 README.md\n"
                    "- 写入 demo.txt：hello\n"
                    "- 编辑 demo.txt：把hello替换为world"
                )
            ]
        )


class MockModelPlugin(Plugin):
    name = "llm.mock"
    provides = ("llm",)

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("llm", MockModel(), self.name)
