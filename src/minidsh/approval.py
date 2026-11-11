import uuid
from typing import Callable, Dict, Optional

from .plugins import Plugin, ServiceContext
from .session import SessionStore


ASK_SENTENCE = (
    "Approval prompts are enabled in this session: operations that require approval may ask through the configured answerers; "
    "without an available answerer, the request fails closed."
)
NEVER_SENTENCE = (
    "Approval prompts are disabled in this session: actions that require approval are rejected automatically — "
    "do not request sandbox escalation (do not set `sandbox_permissions`)."
)


class ApprovalService:
    def __init__(self, policy: str, answerer: Optional[Callable[[str], str]] = None) -> None:
        if policy not in {"ask", "never"}:
            raise ValueError("approval policy must be ask or never")
        self.policy = policy
        self.answerer = answerer

    def context(self) -> str:
        return ASK_SENTENCE if self.policy == "ask" else NEVER_SENTENCE

    def request(
        self,
        tool_name: str,
        call_id: Optional[str],
        session_id: Optional[str],
        session: Optional[SessionStore],
    ) -> str:
        request_id = uuid.uuid4().hex
        reason = f"{tool_name} 请求修改文件"
        if session is not None and session_id is not None:
            payload: Dict[str, object] = {"id": request_id, "toolName": tool_name, "reason": reason}
            if call_id is not None:
                payload["callId"] = call_id
            session.append(session_id, "approval/asked", payload)

        if self.policy == "never":
            outcome = "rejected"
        elif self.answerer is None:
            outcome = "unavailable"
        else:
            try:
                outcome = "allowed-once" if self.answerer(reason) in {"y", "yes", "allowed-once"} else "rejected"
            except Exception:
                outcome = "unavailable"

        if session is not None and session_id is not None:
            session.append(session_id, "approval/decided", {"id": request_id, "outcome": outcome})
        return outcome


class ApprovalPlugin(Plugin):
    name = "core.approval"
    provides = ("approval",)

    def __init__(self, policy: str, answerer: Optional[Callable[[str], str]] = None) -> None:
        self.policy = policy
        self.answerer = answerer

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("approval", ApprovalService(self.policy, self.answerer), self.name)
