import uuid
from typing import Dict, List, Optional

from .events import EventBus
from .llm import Model, ModelResponse
from .plugins import Plugin, ServiceContext
from .session import SessionStore
from .tools import ToolError, ToolRegistry


class Agent:
    def __init__(
        self,
        model: Model,
        tools: ToolRegistry,
        events: EventBus,
        session: Optional[SessionStore] = None,
        max_steps: int = 4,
    ) -> None:
        self.model = model
        self.tools = tools
        self.events = events
        self.session = session
        self.max_steps = max_steps

    def run(self, prompt: str) -> str:
        session_id = uuid.uuid4().hex
        messages: List[Dict[str, object]] = [
            {
                "role": "system",
                "content": "你是 MiniDSH 学习 Agent。优先使用已启用工具，不要虚构工具结果。",
            },
            {"role": "user", "content": prompt},
        ]
        self._record(session_id, "session/start", {"prompt": prompt})
        self._record(session_id, "user/message", {"content": prompt})
        self.events.emit("turn/start", {"session_id": session_id, "prompt": prompt})

        for step in range(self.max_steps):
            response = self.model.complete(messages, self.tools.definitions())
            assistant_message: Dict[str, object] = {
                "role": "assistant",
                "content": response.content,
            }
            if response.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                    for call in response.tool_calls
                ]
            messages.append(assistant_message)
            self._record(session_id, "assistant/message", assistant_message)

            if not response.tool_calls:
                self._record(session_id, "turn/end", {"content": response.content})
                self.events.emit("turn/end", {"session_id": session_id, "content": response.content})
                return response.content

            for call in response.tool_calls:
                self._record(
                    session_id,
                    "tool/call",
                    {"id": call.call_id, "name": call.name, "arguments": call.arguments},
                )
                try:
                    result = self.tools.execute(call.name, call.arguments)
                except ToolError as exc:
                    result = f"工具执行失败: {exc}"
                tool_message = {
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "content": result,
                }
                messages.append(tool_message)
                self._record(session_id, "tool/result", tool_message)

        raise RuntimeError(f"Agent 超过最大步数: {self.max_steps}")

    def _record(self, session_id: str, event_type: str, payload: Dict[str, object]) -> None:
        if self.session is not None:
            self.session.append(session_id, event_type, payload)
