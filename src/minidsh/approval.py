import queue
import threading
import uuid
from typing import Callable, Dict, Optional

from .cancellation import CancellationToken
from .plugins import Plugin, ServiceContext
from .session import SessionStore


ASK_SENTENCE = (
    "Approval prompts are enabled in this session: operations that require approval may ask through the configured answerers; "
    "without an available answerer, the request fails closed."
)
NEVER_SENTENCE = "Approval prompts are disabled in this session: actions that require approval are rejected automatically — do not request sandbox escalation (do not set `sandbox_permissions`)."
POLICY_NOTICE_NAMES = {"ask": "ask the user for approval", "never": "never ask the user for approval"}


class ApprovalService:
    def __init__(self, policy: str, answerer: Optional[Callable[[str], str]] = None) -> None:
        if policy not in {"ask", "never"}:
            raise ValueError("approval policy must be ask or never")
        self.policy = policy
        self.answerer = answerer

    def effective_policy(self, session: Optional[SessionStore], session_id: Optional[str]) -> str:
        if session is None or session_id is None:
            return self.policy
        policy = self.policy
        for event in session.load(session_id):
            if event["type"] == "approval/policy":
                override = event["payload"].get("policy")
                if override not in {"ask", "never"}:
                    raise ValueError(f"会话包含无效 approval policy: {override}")
                policy = override
        return policy

    def set_policy(self, session: SessionStore, session_id: str, policy: str) -> None:
        if policy not in {"ask", "never"}:
            raise ValueError("approval policy must be ask or never")
        previous = self.effective_policy(session, session_id)
        if previous == policy:
            return
        session.append(session_id, "approval/policy", {"policy": policy})
        session.append(
            session_id,
            "user/message",
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f'The user changed the approval policy from "{POLICY_NOTICE_NAMES[previous]}" to "{POLICY_NOTICE_NAMES[policy]}".',
                    }
                ],
                "source": {"kind": "user-approval"},
            },
        )

    def context(self) -> str:
        return ASK_SENTENCE if self.policy == "ask" else NEVER_SENTENCE

    def context_for_session(self, session: Optional[SessionStore], session_id: Optional[str]) -> str:
        return ASK_SENTENCE if self.effective_policy(session, session_id) == "ask" else NEVER_SENTENCE

    def request(
        self,
        tool_name: str,
        call_id: Optional[str],
        session_id: Optional[str],
        session: Optional[SessionStore],
        cancel_token: Optional[CancellationToken] = None,
    ) -> str:
        request_id = uuid.uuid4().hex
        action = "执行 Shell 命令" if tool_name == "bash" else "修改文件"
        reason = f"{tool_name} 请求{action}"
        if session is not None and session_id is not None:
            payload: Dict[str, object] = {"id": request_id, "toolName": tool_name, "reason": reason}
            if call_id is not None:
                payload["callId"] = call_id
            session.append(session_id, "approval/asked", payload)

        policy = self.effective_policy(session, session_id)
        if policy == "never":
            outcome = "rejected"
        elif self.answerer is None:
            outcome = "unavailable"
        else:
            outcome = self._decide(reason, cancel_token)

        if session is not None and session_id is not None:
            session.append(session_id, "approval/decided", {"id": request_id, "outcome": outcome})
        return outcome

    def _decide(self, reason: str, token: Optional[CancellationToken]) -> str:
        outcomes: queue.Queue = queue.Queue(maxsize=1)

        def answer() -> None:
            try:
                allowed = self.answerer(reason) in {"y", "yes", "allowed-once"}
                outcomes.put("allowed-once" if allowed else "rejected")
            except Exception:
                outcomes.put("unavailable")

        threading.Thread(target=answer, daemon=True).start()
        while True:
            try:
                return outcomes.get(timeout=0.05)
            except queue.Empty:
                if token is not None and token.cancelled:
                    return "cancelled"


class ApprovalPlugin(Plugin):
    name = "core.approval"
    provides = ("approval",)

    def __init__(self, policy: str, answerer: Optional[Callable[[str], str]] = None) -> None:
        self.policy = policy
        self.answerer = answerer

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("approval", ApprovalService(self.policy, self.answerer), self.name)
