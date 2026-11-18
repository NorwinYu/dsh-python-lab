from typing import Dict, List


TOOL_NOT_STARTED = "TOOL_NOT_STARTED"
TOOL_OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"

INTERRUPTED_NOT_STARTED = "The tool call was interrupted before the Harness recorded it as started. Retry it if it is still needed."
INTERRUPTED_STARTED = "The tool call was interrupted after it was recorded, but no result was durably recorded. Its outcome is unknown. Decide whether to retry from the tool semantics: retry only if the operation is read-only or idempotent; if it may have side effects, first verify external state or ask the user. Do not retry blindly."
FORKED_NOT_STARTED = "The history inherited by this branch has no record of this tool call starting. The parent session may have executed it after the fork point. Decide whether to retry from the tool semantics: retry only if the operation is read-only or idempotent; if it may have side effects, first verify external state or ask the user. Do not retry blindly."
FORKED_STARTED = "The history inherited by this branch records this tool call starting but does not include its result. The parent session may have completed it after the fork point. Decide whether to retry from the tool semantics: retry only if the operation is read-only or idempotent; if it may have side effects, first verify external state or ask the user. Do not retry blindly."


def open_turn_closers(events: List[Dict[str, object]], cause: str = "interrupted") -> List[Dict[str, object]]:
    if cause not in {"interrupted", "forked"}:
        raise ValueError("unknown recovery reason")
    open_turn = None
    open_step = None
    pending: Dict[str, Dict[str, object]] = {}
    for event in events:
        event_type = event["type"]
        payload = event["payload"]
        if event_type == "turn/start":
            open_turn = payload.get("turn")
            open_step = None
            pending.clear()
        elif event_type == "turn/end":
            open_turn = None
            open_step = None
            pending.clear()
        elif event_type == "step/start":
            open_step = payload.get("step")
        elif event_type == "step/end":
            open_step = None
            pending.clear()
        elif event_type == "assistant/message":
            message = payload.get("message", {})
            content = message.get("content", []) if isinstance(message, dict) else []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool-call":
                    pending[str(block["id"])] = {
                        "turn": payload.get("turn", open_turn),
                        "step": payload.get("step", open_step),
                        "name": str(block.get("name", "unknown")),
                        "started": False,
                    }
        elif event_type == "tool/call":
            call_id = str(payload.get("id", payload.get("callId", "")))
            if call_id in pending:
                pending[call_id]["started"] = True
        elif event_type == "tool/result":
            pending.pop(str(payload.get("toolCallId", "")), None)
    if open_turn is None:
        return []
    closers: List[Dict[str, object]] = []
    for call_id, call in pending.items():
        started = call["started"] is True
        code = TOOL_OUTCOME_UNKNOWN if started else TOOL_NOT_STARTED
        if cause == "interrupted":
            text = INTERRUPTED_STARTED if started else INTERRUPTED_NOT_STARTED
        else:
            text = FORKED_STARTED if started else FORKED_NOT_STARTED
        closers.append(
            {
                "type": "tool/result",
                "payload": {
                    "role": "tool",
                    "toolCallId": call_id,
                    "name": call["name"],
                    "content": [{"type": "text", "text": text}],
                    "isError": True,
                    "source": {"kind": "tool-recovery", "code": code},
                },
            }
        )
    if open_step is not None:
        closers.append({"type": "step/end", "payload": {"turn": open_turn, "step": open_step}})
    closers.append({"type": "turn/end", "payload": {"turn": open_turn, "reason": {"kind": cause}}})
    return closers
