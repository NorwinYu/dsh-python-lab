import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .events import EventBus
from .llm import Model, text_block
from .session import SessionStore
from .system_prompt import SystemPrompt
from .tools import ToolError, ToolRegistry


class Agent:
    def __init__(
        self,
        model: Model,
        tools: ToolRegistry,
        system_prompt: SystemPrompt,
        workspace: Path,
        events: EventBus,
        session: Optional[SessionStore] = None,
        max_steps: int = 4,
    ) -> None:
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.workspace = workspace
        self.events = events
        self.session = session
        self.max_steps = max_steps

    def run(self, prompt: str) -> str:
        session_id = uuid.uuid4().hex
        system = self.system_prompt.render()
        messages: List[Dict[str, object]] = [
            {"role": "user", "content": [text_block(prompt)]},
            {
                "role": "user",
                "content": [text_block(self.system_prompt.runtime_context(self.workspace))],
                "source": {"kind": "runtime-context"},
            },
        ]
        self._record(
            session_id,
            "system/message",
            {"message": {"role": "system", "content": [text_block(system)]}},
        )
        self._record(session_id, "session/start", {"prompt": prompt})
        self._record(session_id, "user/message", messages[0])
        self._record(session_id, "user/message", messages[1])
        self.events.emit("turn/start", {"session_id": session_id, "prompt": prompt})

        for step in range(1, self.max_steps + 1):
            self._record(session_id, "step/start", {"turn": 1, "step": step})
            response = self.model.complete(system, messages, self.tools.definitions(), session_id)
            model_name = getattr(self.model, "model_id", "mock")
            if response.replay_state is not None:
                response_info = response.replay_state.get("response", {})
                if isinstance(response_info, dict):
                    model_name = str(response_info.get("model", model_name))
            source: Dict[str, object] = {
                "kind": "model",
                "provider": getattr(self.model, "provider", "mock"),
                "model": model_name,
            }
            if response.replay_state is not None:
                source["replayState"] = response.replay_state
            assistant_message: Dict[str, object] = {
                "role": "assistant",
                "content": response.blocks,
                "source": source,
            }
            if response.usage is not None:
                assistant_message["usage"] = response.usage
            messages.append(assistant_message)
            self._record(
                session_id,
                "assistant/message",
                {"turn": 1, "step": step, "message": assistant_message, "usage": response.usage},
            )

            if not response.tool_calls:
                reason = response.finish_reason
                self._record(session_id, "step/end", {"turn": 1, "step": step})
                self._record(session_id, "turn/end", {"turn": 1, "reason": reason})
                self.events.emit("turn/end", {"session_id": session_id, "content": response.content})
                return response.content

            for call in response.tool_calls:
                self._record(
                    session_id,
                    "tool/call",
                    {"id": call.call_id, "name": call.name, "arguments": call.arguments},
                )
                is_error = False
                try:
                    result = self.tools.execute(call.name, call.arguments)
                except ToolError as exc:
                    result = f"工具执行失败: {exc}"
                    is_error = True
                tool_message = {
                    "role": "tool",
                    "toolCallId": call.call_id,
                    "name": call.name,
                    "content": [text_block(result)],
                }
                if is_error:
                    tool_message["isError"] = True
                messages.append(tool_message)
                self._record(session_id, "tool/result", tool_message)
            self._record(session_id, "step/end", {"turn": 1, "step": step})

        raise RuntimeError(f"Agent 超过最大步数: {self.max_steps}")

    def _record(self, session_id: str, event_type: str, payload: Dict[str, object]) -> None:
        if self.session is not None:
            self.session.append(session_id, event_type, payload)
