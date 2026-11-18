import json
import re
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
                raise ValueError("tool arguments must be a JSON object")
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
        raise RuntimeError("the current model provider does not support the Files API")


def text_block(text: str) -> Dict[str, object]:
    return {"type": "text", "text": text}


def tool_call_block(call: ToolCall) -> Dict[str, object]:
    return {"type": "tool-call", "id": call.call_id, "name": call.name, "arguments": call.arguments}


@dataclass
class Intent:
    """A deterministic instruction parsed from a prompt.

    MockModel never guesses intent from natural language. Every supported
    action has one explicit prefix, so a lab can prove which tool was called
    and why. Keep the parser boring: predictable inputs, predictable outputs.
    """

    name: str
    arguments: Dict[str, object] = field(default_factory=dict)


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'`":
        return value[1:-1]
    return value


def _parse_edit(text: str) -> Optional[Intent]:
    """Parse ``edit <path>: replace <old> with <new>`` (or ``->``)."""
    match = re.match(r"edit\s+(.+?)\s*:\s*replace\s+(.+?)\s+with\s+(.+)$", text, re.IGNORECASE | re.DOTALL)
    if match is None:
        match = re.match(r"edit\s+(.+?)\s*:\s*(.+?)\s*(?:->|=>)\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    return Intent(
        "edit",
        {
            "file_path": _strip_quotes(match.group(1)),
            "old_string": _strip_quotes(match.group(2)),
            "new_string": _strip_quotes(match.group(3)),
        },
    )


def _parse_chinese_intent(text: str) -> Optional[Intent]:
    """Parse the legacy Chinese trigger grammar.

    The lab started in Chinese, so every English intent keeps a Chinese alias.
    Aliases produce exactly the same Intent objects, which means a session can
    mix languages turn by turn without changing any downstream behaviour.
    """

    def match(pattern: str) -> Optional[re.Match]:
        return re.match(pattern, text, re.DOTALL)

    if text in {"任务列表"}:
        return Intent("job-list")
    found = match(r"读取任务\s+(\S+)$")
    if found:
        return Intent("job-output", {"job_id": found.group(1)})
    found = match(r"终止任务\s+(\S+)$")
    if found:
        return Intent("job-kill", {"job_id": found.group(1)})
    found = match(r"运行代码\s+(.+)$")
    if found:
        return Intent("run-code", {"json": found.group(1).strip()})
    found = match(r"创建目标\s+(.+)$")
    if found:
        objective, separator, rounds = found.group(1).partition("|")
        arguments: Dict[str, object] = {"objective": objective.strip()}
        if separator:
            arguments["max_goal_rounds"] = int(rounds)
        return Intent("goal-create", arguments)
    if text in {"读取目标"}:
        return Intent("goal-get")
    found = match(r"更新目标\s+(.+)$")
    if found:
        return Intent("goal-update", {"json": found.group(1).strip()})
    found = match(r"定时任务\s+(.+)$")
    if found:
        return Intent("schedule-create", {"json": found.group(1).strip()})
    if text in {"定时列表"}:
        return Intent("schedule-list")
    found = match(r"定时查看\s+(\S+)$")
    if found:
        return Intent("schedule-view", {"id": found.group(1)})
    found = match(r"定时编辑\s+(.+)$")
    if found:
        return Intent("schedule-edit", {"json": found.group(1).strip()})
    found = match(r"提问\s+(.+)$")
    if found:
        return Intent("ask", {"question": found.group(1).strip()})
    found = match(r"使用技能\s+(\S+)$")
    if found:
        return Intent("skill", {"name": found.group(1)})
    found = match(r"待办\s+(.+)$")
    if found:
        return Intent("todo", {"task": found.group(1).strip()})
    found = match(r"获取\s+(\S+)$")
    if found:
        return Intent("fetch", {"url": found.group(1).strip()})
    found = match(r"搜索\s+(.+)$")
    if found:
        return Intent("search", {"query": found.group(1).strip()})
    found = match(r"工作流后台\s+(.+)$")
    if found:
        return Intent("workflow", {"items": found.group(1), "background": True})
    found = match(r"工作流\s+(.+)$")
    if found:
        return Intent("workflow", {"items": found.group(1), "background": False})
    found = match(r"委派\s+(.+)$")
    if found:
        request = found.group(1).strip()
        description, separator, task = request.partition("|")
        return Intent(
            "subagent",
            {"description": description.strip() or "委派任务", "prompt": task.strip() if separator else request},
        )
    found = match(r"派生\s+(.+)$")
    if found:
        return Intent("subagent-fork", {"prompt": found.group(1).strip()})
    if text in {"子代理列表"}:
        return Intent("agent-list")
    found = match(r"发送消息\s+([^：:]+)[：:]\s*(.+)$")
    if found:
        return Intent("message", {"agent": found.group(1).strip(), "message": found.group(2).strip()})
    found = match(r"中断子代理\s+(\S+)$")
    if found:
        return Intent("interrupt", {"agent": found.group(1)})
    found = match(r"等待子代理输入(?:\s+(\d+))?$")
    if found:
        return Intent("wait", {"timeout_ms": int(found.group(1) or "30000")})
    found = match(r"执行后台\s+(.+)$")
    if found:
        return Intent("run", {"command": found.group(1).strip(), "background": True})
    found = match(r"执行\s+(.+)$")
    if found:
        return Intent("run", {"command": found.group(1).strip(), "background": False})
    found = match(r"写入\s+(.+?)\s*[：:]\s*(.*)$")
    if found:
        return Intent("write", {"file_path": _strip_quotes(found.group(1)), "content": found.group(2)})
    found = match(r"编辑\s+(.+?)\s*[：:]\s*把(.+?)替换为(.+)$")
    if found:
        return Intent(
            "edit",
            {
                "file_path": _strip_quotes(found.group(1)),
                "old_string": _strip_quotes(found.group(2)),
                "new_string": _strip_quotes(found.group(3)),
            },
        )
    found = match(r"读取\s+(.+)$")
    if found:
        return Intent("read", {"file_path": _strip_quotes(found.group(1))})
    if "目录" in text:
        return Intent("list")
    return None


def parse_intent(prompt: str) -> Intent:
    """Map a mock prompt onto an Intent.

    Both English and Chinese grammars are accepted; the grammar is documented
    in ``docs/mock-model.md``. Anything that does not match returns
    ``Intent("unknown")`` and the model answers with the built-in cheat sheet
    instead of inventing a tool call.
    """
    intent = _parse_english_intent(prompt)
    if intent.name != "unknown":
        return intent
    chinese = _parse_chinese_intent(prompt.strip())
    return chinese if chinese is not None else intent


def _parse_english_intent(prompt: str) -> Intent:
    """Parse the English trigger grammar."""
    text = prompt.strip()
    lowered = text.lower()

    def match(pattern: str, value: str = text) -> Optional[re.Match]:
        return re.match(pattern, value, re.IGNORECASE | re.DOTALL)

    if lowered in {"jobs", "job list", "list jobs"}:
        return Intent("job-list")
    found = match(r"job\s+output\s+(\S+)")
    if found:
        return Intent("job-output", {"job_id": found.group(1)})
    found = match(r"(?:kill|stop)\s+job\s+(\S+)")
    if found:
        return Intent("job-kill", {"job_id": found.group(1)})

    found = match(r"(?:run\s+code|code)\s+(.+)$")
    if found:
        return Intent("run-code", {"json": found.group(1).strip()})
    found = match(r"run\s+in\s+background\s+(.+)$")
    if found:
        return Intent("run", {"command": found.group(1).strip(), "background": True})
    found = match(r"background\s+(.+)$")
    if found:
        return Intent("run", {"command": found.group(1).strip(), "background": True})
    found = match(r"run\s+(.+)$")
    if found:
        return Intent("run", {"command": found.group(1).strip(), "background": False})

    found = match(r"goal\s+(?:create|new)\s+(.+)$")
    if found:
        objective, separator, rounds = found.group(1).partition("|")
        arguments: Dict[str, object] = {"objective": objective.strip()}
        if separator:
            arguments["max_goal_rounds"] = int(rounds)
        return Intent("goal-create", arguments)
    if lowered in {"goal", "goal get", "goal show", "goal status"}:
        return Intent("goal-get")
    found = match(r"goal\s+update\s+(.+)$")
    if found:
        return Intent("goal-update", {"json": found.group(1).strip()})

    found = match(r"schedule\s+(?:create|add)\s+(.+)$")
    if found:
        return Intent("schedule-create", {"json": found.group(1).strip()})
    if lowered in {"schedule", "schedule list"}:
        return Intent("schedule-list")
    found = match(r"schedule\s+view\s+(\S+)")
    if found:
        return Intent("schedule-view", {"id": found.group(1)})
    found = match(r"schedule\s+edit\s+(.+)$")
    if found:
        return Intent("schedule-edit", {"json": found.group(1).strip()})

    found = match(r"(?:ask|question)\s+(.+)$")
    if found:
        return Intent("ask", {"question": found.group(1).strip()})
    found = match(r"todo\s+(.+)$")
    if found:
        return Intent("todo", {"task": found.group(1).strip()})
    found = match(r"(?:fetch|web-fetch)\s+(\S+)")
    if found:
        return Intent("fetch", {"url": found.group(1).strip()})
    found = match(r"(?:search|web-search)\s+(.+)$")
    if found:
        return Intent("search", {"query": found.group(1).strip()})
    found = match(r"skill\s+(\S+)")
    if found:
        return Intent("skill", {"name": found.group(1)})

    found = match(r"delegate\s+(.+)$")
    if found:
        request = found.group(1).strip()
        description, separator, task = request.partition("|")
        return Intent(
            "subagent",
            {"description": description.strip() or "Delegated task", "prompt": task.strip() if separator else request},
        )
    found = match(r"(?:fork\s+agent|subagent\s+fork)\s+(.+)$")
    if found:
        return Intent("subagent-fork", {"prompt": found.group(1).strip()})
    if lowered in {"agents", "agent list", "subagents"}:
        return Intent("agent-list")
    found = match(r"message\s+([^:]+):\s*(.+)$")
    if found:
        return Intent("message", {"agent": found.group(1).strip(), "message": found.group(2).strip()})
    found = match(r"interrupt\s+(\S+)$")
    if found:
        return Intent("interrupt", {"agent": found.group(1)})
    found = match(r"wait\s+(?:for\s+)?agent(?:\s+(\d+))?$")
    if found:
        return Intent("wait", {"timeout_ms": int(found.group(1) or "30000")})

    found = match(r"workflow\s+(?:in\s+)?background\s+(.+)$")
    if found:
        return Intent("workflow", {"items": found.group(1), "background": True})
    found = match(r"workflow\s+(.+)$")
    if found:
        return Intent("workflow", {"items": found.group(1), "background": False})

    edit = _parse_edit(text)
    if edit is not None:
        return edit
    found = match(r"write\s+(.+?)\s*[:：]\s*(.*)$")
    if found:
        return Intent("write", {"file_path": _strip_quotes(found.group(1)), "content": found.group(2)})
    found = match(r"read\s+(.+)$")
    if found:
        return Intent("read", {"file_path": _strip_quotes(found.group(1))})
    if lowered.startswith(("list", "ls", "glob")) or "directory" in lowered:
        return Intent("list")
    return Intent("unknown")


MOCK_HELP = (
    "MockModel received your message but found no known intent.\n"
    "Explicit prefixes work in English or Chinese:\n"
    "- list / 目录\n"
    "- read README.md / 读取 README.md\n"
    "- write demo.txt: hello / 写入 demo.txt：hello\n"
    "- edit demo.txt: replace hello with world / 编辑 demo.txt：把hello替换为world\n"
    "- run printf hello / 执行 printf hello\n"
    "- ask Do you want to continue? / 提问 是否继续？\n"
    "- todo Finish the lab / 待办 完成实验"
)


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
                return ModelResponse(blocks=[text_block("Subagent result received and summarized.")])
        prompt = ""
        for message in reversed(messages):
            if message["role"] != "user":
                continue
            source = message.get("source", {})
            if isinstance(source, dict) and source.get("kind") in {"runtime-context", "skill-catalog"}:
                continue
            content = message["content"]
            if isinstance(content, list):
                prompt = "".join(str(block.get("text", "")) for block in content if isinstance(block, dict)).strip()
            else:
                prompt = str(content).strip()
            if isinstance(source, dict) and source.get("kind") == "skill-invocation" and "<skill_instructions>\n" in prompt:
                prompt = prompt.split("<skill_instructions>\n", 1)[1].rsplit("\n</skill_instructions>", 1)[0].strip()
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

        intent = parse_intent(prompt)

        if messages[-1]["role"] == "tool":
            last = messages[-1]
            if intent.name == "edit" and last.get("name") == "read" and not last.get("isError"):
                call = ToolCall(name="edit", arguments=dict(intent.arguments), call_id="mock-2")
                return ModelResponse(blocks=[text_block("Read complete, applying the edit now."), tool_call_block(call)])
            content = last["content"]
            if isinstance(content, list):
                text = "".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
            else:
                text = str(content)
            return ModelResponse(blocks=[text_block(f"Tool finished:\n{text}")])

        if intent.name == "run-code" and "run_code" in available:
            call = ToolCall(name="run_code", arguments=json.loads(str(intent.arguments["json"])), call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "goal-create" and "create_goal" in available:
            call = ToolCall(name="create_goal", arguments=dict(intent.arguments), call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "goal-get" and "get_goal" in available:
            return ModelResponse(blocks=[tool_call_block(ToolCall(name="get_goal", arguments={}, call_id="mock-1"))])

        if intent.name == "goal-update" and "update_goal" in available:
            call = ToolCall(name="update_goal", arguments=json.loads(str(intent.arguments["json"])), call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "schedule-create" and "schedule_create" in available:
            call = ToolCall(name="schedule_create", arguments=json.loads(str(intent.arguments["json"])), call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "schedule-list" and "schedule_list" in available:
            return ModelResponse(blocks=[tool_call_block(ToolCall(name="schedule_list", arguments={}, call_id="mock-1"))])

        if intent.name == "schedule-view" and "schedule_view" in available:
            call = ToolCall(name="schedule_view", arguments={"id": intent.arguments["id"]}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "schedule-edit" and "schedule_edit" in available:
            call = ToolCall(name="schedule_edit", arguments=json.loads(str(intent.arguments["json"])), call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "ask" and "ask_user_question" in available:
            call = ToolCall(
                name="ask_user_question",
                arguments={"questions": [{"id": "question-1", "question": intent.arguments["question"]}]},
                call_id="mock-1",
            )
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "skill" and "skill" in available:
            call = ToolCall(name="skill", arguments={"name": intent.arguments["name"]}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "todo" and "todo_write" in available:
            call = ToolCall(
                name="todo_write",
                arguments={"todos": [{"content": intent.arguments["task"], "status": "in_progress"}]},
                call_id="mock-1",
            )
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "fetch" and "web_fetch" in available:
            call = ToolCall(name="web_fetch", arguments={"url": intent.arguments["url"]}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "search" and "web_search" in available:
            call = ToolCall(name="web_search", arguments={"queries": [intent.arguments["query"]]}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "workflow" and "workflow" in available:
            items = [item.strip() for item in str(intent.arguments["items"]).split("|") if item.strip()] or ["hello"]
            call = ToolCall(
                name="workflow",
                arguments={
                    "script": "phase('agents'); return await parallel(args.items.map(item => () => agent(item)))",
                    "meta": {"name": "mock-fanout", "description": "Run the requested parallel agents"},
                    "args": {"items": items},
                    "run_in_background": bool(intent.arguments["background"]),
                },
                call_id="mock-1",
            )
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "subagent" and "subagent" in available:
            call = ToolCall(name="subagent", arguments=dict(intent.arguments), call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "subagent-fork" and "subagent_fork" in available:
            call = ToolCall(
                name="subagent_fork",
                arguments={"description": "Fork with inherited history", "prompt": intent.arguments["prompt"]},
                call_id="mock-1",
            )
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "agent-list" and "list_agents" in available:
            return ModelResponse(blocks=[tool_call_block(ToolCall(name="list_agents", arguments={}, call_id="mock-1"))])

        if intent.name == "message" and "send_message" in available:
            call = ToolCall(
                name="send_message",
                arguments={"agent": intent.arguments["agent"], "message": intent.arguments["message"]},
                call_id="mock-1",
            )
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "interrupt" and "interrupt_agent" in available:
            call = ToolCall(name="interrupt_agent", arguments={"agent": intent.arguments["agent"]}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "wait" and "wait_agent" in available:
            call = ToolCall(name="wait_agent", arguments={"timeout_ms": intent.arguments["timeout_ms"]}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "run" and "bash" in available:
            arguments = {
                "description": "Run the requested shell command",
                "command": intent.arguments["command"],
                "run_in_background": bool(intent.arguments["background"]),
            }
            call = ToolCall(name="bash", arguments=arguments, call_id="mock-1")
            return ModelResponse(blocks=[text_block("Running the shell command; approval may be required."), tool_call_block(call)])

        if intent.name == "job-list" and "job_list" in available:
            call = ToolCall(name="job_list", arguments={}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "job-output" and "job_output" in available:
            call = ToolCall(name="job_output", arguments={"job_id": intent.arguments["job_id"], "wait": True}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "job-kill" and "job_kill" in available:
            call = ToolCall(name="job_kill", arguments={"job_id": intent.arguments["job_id"]}, call_id="mock-1")
            return ModelResponse(blocks=[tool_call_block(call)])

        if intent.name == "write" and "write" in available:
            call = ToolCall(
                name="write",
                arguments={"file_path": intent.arguments["file_path"], "content": intent.arguments["content"]},
                call_id="mock-1",
            )
            return ModelResponse(blocks=[text_block("Writing the file; approval may be required."), tool_call_block(call)])

        if intent.name == "edit" and "read" in available:
            call = ToolCall(name="read", arguments={"file_path": intent.arguments["file_path"]}, call_id="mock-1")
            return ModelResponse(blocks=[text_block("Reading the file before editing it."), tool_call_block(call)])

        if intent.name == "list" and "glob" in available:
            call = ToolCall(name="glob", arguments={"pattern": "*"}, call_id="mock-1")
            return ModelResponse(blocks=[text_block("Listing the current directory."), tool_call_block(call)])

        if intent.name == "read" and "read" in available:
            call = ToolCall(name="read", arguments={"file_path": intent.arguments["file_path"]}, call_id="mock-1")
            return ModelResponse(blocks=[text_block("Reading the requested file."), tool_call_block(call)])

        return ModelResponse(blocks=[text_block(MOCK_HELP)])


class MockModelPlugin(Plugin):
    name = "llm.mock"
    provides = ("llm",)

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("llm", MockModel(), self.name)
