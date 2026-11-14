from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .cancellation import CancellationToken
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
        cancel_token: Optional[CancellationToken] = None,
        on_event: Optional[Callable[[Dict[str, object]], None]] = None,
    ) -> ModelResponse:
        pass

    def list_models(self) -> Dict[str, object]:
        return {"entries": [{"id": self.model_id, "name": self.model_id, "inputModalities": ["text"]}], "skipped": 0}

    def list_files(self) -> Dict[str, object]:
        raise RuntimeError("当前模型提供方不支持 Files API")


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
        cancel_token: Optional[CancellationToken] = None,
        on_event: Optional[Callable[[Dict[str, object]], None]] = None,
    ) -> ModelResponse:
        if cancel_token is not None:
            cancel_token.throw_if_cancelled()
        if messages:
            source = messages[-1].get("source", {})
            if isinstance(source, dict) and source.get("kind") == "subagent-settled":
                return ModelResponse(blocks=[text_block("已收到子代理结果并完成汇总。")])
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
        if system.startswith("You are now acting as a compaction engine"):
            summary = (
                "## Primary Request and Intent\n- (none)\n\n"
                "## Key Technical Concepts\n- (none)\n\n"
                "## Files and Code\n- (none)\n\n"
                "## Errors and Fixes\n- (none)\n\n"
                "## Pending Jobs\n- (none)\n\n"
                "## Current Work\n- (none)\n\n"
                "## Next Step\n- (none)\n\n"
                "## Critical Context\n- (none)"
            )
            return ModelResponse(blocks=[text_block(summary)])
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

        if prompt.startswith("提问") and "ask_user_question" in available:
            question = prompt[2:].strip()
            call = ToolCall(
                name="ask_user_question",
                arguments={"questions": [{"id": "question-1", "question": question}]},
                call_id="mock-1",
            )
            return ModelResponse(blocks=[tool_call_block(call)])

        if prompt.startswith("待办") and "todo_write" in available:
            task = prompt[2:].strip()
            call = ToolCall(
                name="todo_write",
                arguments={"todos": [{"content": task, "status": "in_progress"}]},
                call_id="mock-1",
            )
            return ModelResponse(blocks=[tool_call_block(call)])

        if prompt.startswith("获取") and "web_fetch" in available:
            call = ToolCall(name="web_fetch", arguments={"url": prompt[2:].strip()}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if prompt.startswith("搜索") and "web_search" in available:
            call = ToolCall(name="web_search", arguments={"queries": [prompt[2:].strip()]}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if prompt.startswith("工作流") and "workflow" in available:
            background = prompt.startswith("工作流后台")
            items = [item.strip() for item in prompt[5 if background else 3:].strip().split("|") if item.strip()] or ["你好"]
            call = ToolCall(
                name="workflow",
                arguments={
                    "script": "phase('agents'); return await parallel(args.items.map(item => () => agent(item)))",
                    "meta": {"name": "mock-fanout", "description": "Run the requested parallel agents"},
                    "args": {"items": items},
                    "run_in_background": background,
                },
                call_id="mock-1",
            )
            return ModelResponse(blocks=[tool_call_block(call)])

        if prompt.startswith("委派") and "subagent" in available:
            request = prompt[2:].strip()
            description, separator, task = request.partition("|")
            call = ToolCall(name="subagent", arguments={"description": description.strip() or "委派任务", "prompt": task.strip() if separator else request}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if prompt.startswith("派生") and "subagent_fork" in available:
            call = ToolCall(name="subagent_fork", arguments={"description": "继承历史子任务", "prompt": prompt[2:].strip()}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if prompt.startswith("子代理列表") and "list_agents" in available:
            return ModelResponse(blocks=[tool_call_block(ToolCall(name="list_agents", arguments={}, call_id="mock-1"))])

        if prompt.startswith("发送消息") and "send_message" in available:
            agent, message = prompt[4:].strip().split("：", 1)
            call = ToolCall(name="send_message", arguments={"agent": agent, "message": message}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if prompt.startswith("中断子代理") and "interrupt_agent" in available:
            call = ToolCall(name="interrupt_agent", arguments={"agent": prompt[5:].strip()}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if prompt.startswith("等待子代理输入") and "wait_agent" in available:
            call = ToolCall(name="wait_agent", arguments={"timeout_ms": int(prompt[6:].strip() or "30000")}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if (prompt.startswith("执行") or prompt.startswith("执行后台")) and "bash" in available:
            background = prompt.startswith("执行后台")
            command = prompt[4:].strip() if background else prompt[2:].strip()
            arguments = {
                "description": "Run the requested shell command",
                "command": command,
                "run_in_background": background,
            }
            call = ToolCall(name="bash", arguments=arguments, call_id="mock-1")
            return ModelResponse(blocks=[text_block("我准备执行 Shell 命令，请先完成审批。"), tool_call_block(call)])

        if prompt.startswith("任务列表") and "job_list" in available:
            call = ToolCall(name="job_list", arguments={}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if prompt.startswith("读取任务") and "job_output" in available:
            job_id = prompt[4:].strip()
            call = ToolCall(name="job_output", arguments={"job_id": job_id, "wait": True}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if prompt.startswith("终止任务") and "job_kill" in available:
            job_id = prompt[4:].strip()
            call = ToolCall(name="job_kill", arguments={"job_id": job_id}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

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
