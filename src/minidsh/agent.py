import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .compaction import CompactionService
from .events import EventBus
from .llm import Model, text_block
from .observations import ObservationStore
from .session import SessionStore
from .system_prompt import SystemPrompt
from .tools import ToolError, ToolRegistry


class Agent:
    def __init__(
        self,
        model: Model,
        tools: ToolRegistry,
        system_prompt: SystemPrompt,
        runtime_context: str,
        events: EventBus,
        session: Optional[SessionStore] = None,
        observations: Optional[ObservationStore] = None,
        compaction: Optional[CompactionService] = None,
        max_steps: int = 4,
    ) -> None:
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.runtime_context = runtime_context
        self.events = events
        self.session = session
        self.observations = observations
        self.compaction = compaction
        self.max_steps = max_steps

    def run(self, prompt: str, session_id: Optional[str] = None, run_id: Optional[str] = None) -> str:
        system = self.system_prompt.render()
        messages: List[Dict[str, object]] = []
        turn = 1
        resumed = session_id is not None
        if resumed:
            if self.session is None:
                raise RuntimeError("resume 需要启用 session.jsonl")
            events = self.session.load(session_id)
            if not events:
                raise RuntimeError(f"unknown session {session_id}")
            messages = self.session.derive_messages(session_id)
            turn = self.session.latest_turn(session_id) + 1
            self._restore_observations(events)
            previous_system = self.session.derive_system(session_id)
            if previous_system != system:
                self._record_system(session_id, system)
            self._record(session_id, "session/resume", {"turn": turn})
        else:
            session_id = uuid.uuid4().hex
            self._record_system(session_id, system)
            self._record(session_id, "session/start", {"prompt": prompt})

        messages = [message for message in messages if not self._is_runtime_context(message)]
        user_message = {"role": "user", "content": [text_block(prompt)]}
        runtime_message = {
            "role": "user",
            "content": [text_block(self.runtime_context)],
            "source": {"kind": "runtime-context"},
        }
        messages.extend([user_message, runtime_message])
        self._record(session_id, "user/message", user_message)
        self._record(session_id, "user/message", runtime_message)
        self._record(session_id, "turn/start", {"turn": turn})
        self.events.emit("turn/start", {"session_id": session_id, "prompt": prompt, "run_id": run_id})

        for step in range(1, self.max_steps + 1):
            self._record(session_id, "step/start", {"turn": turn, "step": step})
            if self.compaction is not None:
                messages = self.compaction.compact_if_needed(system, messages, self.tools.definitions(), session_id)
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
                {"turn": turn, "step": step, "message": assistant_message, "usage": response.usage},
            )
            self.events.emit("assistant/message", {"session_id": session_id, "message": assistant_message})

            if not response.tool_calls:
                reason = response.finish_reason
                self._record(session_id, "step/end", {"turn": turn, "step": step})
                self._record(session_id, "turn/end", {"turn": turn, "reason": reason})
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
                    result = self.tools.execute(call.name, call.arguments, session_id, call.call_id)
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
            self._record(session_id, "step/end", {"turn": turn, "step": step})

        raise RuntimeError(f"Agent 超过最大步数: {self.max_steps}")

    def compact(self, session_id: str) -> str:
        if self.session is None or self.compaction is None:
            raise RuntimeError("压缩需要启用 session.jsonl 和 core.compaction")
        messages = self.session.derive_messages(session_id)
        compacted = self.compaction.compact_if_needed(
            self.system_prompt.render(),
            messages,
            self.tools.definitions(),
            session_id,
            force=True,
        )
        first = compacted[0]
        content = first.get("content", [])
        if not isinstance(content, list):
            raise RuntimeError("压缩结果格式错误")
        return "".join(str(block.get("text", "")) for block in content if isinstance(block, dict))

    @staticmethod
    def _is_runtime_context(message: Dict[str, object]) -> bool:
        source = message.get("source", {})
        return isinstance(source, dict) and source.get("kind") == "runtime-context"

    def _record_system(self, session_id: str, system: str) -> None:
        self._record(
            session_id,
            "system/message",
            {"message": {"role": "system", "content": [text_block(system)]}},
        )

    def _restore_observations(self, events: List[Dict[str, object]]) -> None:
        if self.observations is None:
            return
        for event in events:
            if event["type"] != "fs/observed":
                continue
            payload = event["payload"]
            version = payload.get("version")
            if isinstance(version, list) and len(version) == 2:
                self.observations.restore(str(event["session_id"]), Path(str(payload["path"])), (int(version[0]), int(version[1])))

    def _record(self, session_id: str, event_type: str, payload: Dict[str, object]) -> None:
        if self.session is not None:
            self.session.append(session_id, event_type, payload)
