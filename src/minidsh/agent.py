import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .approval import ApprovalService
from .cancellation import CancellationToken, RunCancelled
from .compaction import CompactionService
from .events import EventBus
from .images import load_image_blocks
from .llm import Model, text_block
from .observations import ObservationStore
from .sandbox import SandboxPolicy
from .session import SessionStore
from .stream_codec import pack_stream
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
        workspace: Optional[Path] = None,
        allow_outside: bool = False,
        streaming: bool = True,
        record_stream: bool = True,
        sandbox_policy: Optional[SandboxPolicy] = None,
        approval_service: Optional[ApprovalService] = None,
        subagents: Optional[object] = None,
        skills: Optional[object] = None,
        schedules: Optional[object] = None,
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
        self.workspace = (workspace or Path.cwd()).resolve()
        self.allow_outside = allow_outside
        self.streaming = streaming
        self.record_stream = record_stream
        self.sandbox_policy = sandbox_policy
        self.approval_service = approval_service
        self.subagents = subagents
        self.skills = skills
        self.schedules = schedules

    def run(
        self,
        prompt: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        cancel_token: Optional[CancellationToken] = None,
        image_paths: Optional[List[str]] = None,
        new_session_id: Optional[str] = None,
        message_source: Optional[Dict[str, object]] = None,
    ) -> str:
        if session_id is not None and self.session is not None:
            with self.session.write_lease(session_id):
                return self._run(prompt, session_id, run_id, cancel_token, image_paths, new_session_id, message_source)
        return self._run(prompt, session_id, run_id, cancel_token, image_paths, new_session_id, message_source)

    def _run(
        self,
        prompt: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        cancel_token: Optional[CancellationToken] = None,
        image_paths: Optional[List[str]] = None,
        new_session_id: Optional[str] = None,
        message_source: Optional[Dict[str, object]] = None,
    ) -> str:
        token = cancel_token or CancellationToken(run_id)
        token.throw_if_cancelled()
        image_blocks = load_image_blocks(self.workspace, image_paths or [], self.allow_outside)
        system = self.system_prompt.render()
        messages: List[Dict[str, object]] = []
        turn = 1
        resumed = session_id is not None
        if resumed:
            if self.session is None:
                raise RuntimeError("resume 需要启用 session.jsonl")
            self.session.repair(session_id)
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
            session_id = new_session_id or uuid.uuid4().hex
            self._record_system(session_id, system)
            self._record(session_id, "session/start", {"prompt": prompt})

        messages = [message for message in messages if not self._is_runtime_context(message)]
        user_message = {"role": "user", "content": [text_block(prompt)] + image_blocks}
        if message_source is not None:
            user_message["source"] = message_source
        runtime_message = {
            "role": "user",
            "content": [text_block(self._current_runtime_context(session_id))],
            "source": {"kind": "runtime-context"},
        }
        messages.extend([user_message, runtime_message])
        self._record(session_id, "user/message", user_message)
        if message_source is not None and message_source.get("kind") == "schedule":
            self.events.emit("schedule/inbox", {"session_id": session_id, "source": message_source, "run_id": run_id})
        self._record(session_id, "user/message", runtime_message)
        self._record(session_id, "turn/start", {"turn": turn})
        self.events.emit("turn/start", {"session_id": session_id, "prompt": prompt, "run_id": run_id})
        if self.subagents is not None:
            self.subagents.set_root_running(session_id, True)
        try:
            return self._run_steps(system, messages, session_id, turn, run_id, token, prompt)
        except RunCancelled:
            reason = {"kind": "aborted"}
            self._record(session_id, "turn/end", {"turn": turn, "reason": reason})
            self.events.emit("turn/end", {"session_id": session_id, "content": "", "reason": reason, "run_id": run_id})
            raise
        finally:
            if self.subagents is not None:
                self.subagents.set_root_running(session_id, False)

    def _run_steps(
        self,
        system: str,
        messages: List[Dict[str, object]],
        session_id: str,
        turn: int,
        run_id: Optional[str],
        token: CancellationToken,
        user_prompt: str,
    ) -> str:
        for step in range(1, self.max_steps + 1):
            token.throw_if_cancelled()
            if self.subagents is not None:
                inputs = self.subagents.consume_inputs(session_id)
                for message in inputs:
                    messages.append(message)
                    self._record(session_id, "user/message", message)
            if self.schedules is not None:
                scheduled_inputs = self.schedules.take_pending(session_id)
                for scheduled in scheduled_inputs:
                    message = {"role": "user", "content": [text_block(scheduled["text"])], "source": scheduled["source"]}
                    messages.append(message)
                    self._record(session_id, "user/message", message)
                    self.events.emit("schedule/inbox", {"session_id": session_id, "source": scheduled["source"], "run_id": run_id})
            if self.skills is not None:
                skill_inputs = self.skills.before_step(session_id, step, user_prompt)
                for message in skill_inputs:
                    messages.append(message)
                    self._record(session_id, "user/message", message)
            self._record(session_id, "step/start", {"turn": turn, "step": step})
            if self.compaction is not None:
                messages = self.compaction.compact_if_needed(
                    system,
                    messages,
                    self.tools.definitions(),
                    session_id,
                    cancel_token=token,
                )
            saw_delta = False
            stream_events: List[Dict[str, object]] = []

            def forward_model_event(event: Dict[str, object]) -> None:
                nonlocal saw_delta
                stream_events.append({**event, "time": int(time.time() * 1000)})
                event_type = event.get("type")
                if event_type == "usage":
                    self.events.emit("assistant/usage", {"session_id": session_id, "run_id": run_id, "turn": turn, "step": step, **event})
                    return
                if event_type not in {"text-delta", "reasoning-delta", "tool-call-delta", "block-start"}:
                    return
                saw_delta = True
                self.events.emit(
                    "assistant/delta",
                    {"session_id": session_id, "run_id": run_id, "turn": turn, "step": step, **event},
                )

            response = self.model.complete(
                system,
                messages,
                self.tools.definitions(),
                session_id,
                token,
                forward_model_event if self.streaming else None,
            )
            if self.streaming and not saw_delta and response.content:
                fallback_chunk = {
                    "session_id": session_id,
                    "run_id": run_id,
                    "turn": turn,
                    "step": step,
                    "type": "text-delta",
                    "index": 0,
                    "text": response.content,
                    "fallback": True,
                }
                stream_events.append({"type": "text-delta", "index": 0, "text": response.content, "time": int(time.time() * 1000)})
                self.events.emit("assistant/delta", fallback_chunk)
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
            event_payload: Dict[str, object] = {"turn": turn, "step": step, "message": assistant_message, "usage": response.usage}
            if self.record_stream:
                stream_events.append(
                    {
                        "type": "finish",
                        "time": int(time.time() * 1000),
                        "reason": response.finish_reason,
                        "replayState": response.replay_state,
                    }
                )
                event_payload["stream"] = pack_stream(stream_events)
            self._record(session_id, "assistant/message", event_payload)
            self.events.emit("assistant/message", {"session_id": session_id, "message": assistant_message, "run_id": run_id})

            if not response.tool_calls:
                if self.subagents is not None and self.subagents.wait_for_parent_input(session_id, token):
                    self._record(session_id, "step/end", {"turn": turn, "step": step})
                    continue
                reason = response.finish_reason
                self._record(session_id, "step/end", {"turn": turn, "step": step})
                self._record(session_id, "turn/end", {"turn": turn, "reason": reason})
                self.events.emit("turn/end", {"session_id": session_id, "content": response.content, "reason": reason, "run_id": run_id})
                return response.content

            for call in response.tool_calls:
                token.throw_if_cancelled()
                self._record(
                    session_id,
                    "tool/call",
                    {"id": call.call_id, "name": call.name, "arguments": call.arguments},
                )
                is_error = False
                try:
                    result = self.tools.execute(
                        call.name,
                        call.arguments,
                        session_id,
                        call.call_id,
                        run_id,
                        token,
                    )
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
        if self.session is None:
            raise RuntimeError("压缩需要启用 session.jsonl 和 core.compaction")
        with self.session.write_lease(session_id):
            return self._compact(session_id)

    def _compact(self, session_id: str) -> str:
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

    def _current_runtime_context(self, session_id: str) -> str:
        if self.sandbox_policy is None or self.approval_service is None:
            return self.runtime_context
        sandbox_context = self.sandbox_policy.context()
        if self.session is not None:
            sandbox_context = self.sandbox_policy.resolve(self.session, session_id).context()
        approval_context = self.approval_service.context_for_session(self.session, session_id)
        return self.system_prompt.runtime_context(self.workspace, sandbox_context, approval_context)

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
