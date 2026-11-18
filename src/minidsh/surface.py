import copy
from typing import Dict, List


SURFACE_EVENT_TYPES = {"system/message", "user/message", "assistant/message", "tool/result"}


class SurfaceError(ValueError):
    pass


def _valid_seq(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _source_references(event: Dict[str, object], shadowed: List[int]) -> None:
    sources = event.get("sourceEventSeqs")
    if sources is None:
        if shadowed:
            raise SurfaceError("replacement must reference every shadowed node")
        return
    if not isinstance(sources, list) or not sources or any(not _valid_seq(source) for source in sources):
        raise SurfaceError("sourceEventSeqs must be a non-empty array of non-negative integers")
    if len(set(sources)) != len(sources):
        raise SurfaceError("sourceEventSeqs must not contain duplicates")
    if any(source >= event["seq"] for source in sources):
        raise SurfaceError("sourceEventSeqs may only reference earlier events")
    missing = [source for source in shadowed if source not in sources]
    if missing:
        raise SurfaceError(f"sourceEventSeqs is missing shadowed nodes: {missing}")


def _validate_tool_rewrite(event: Dict[str, object], original: Dict[str, object]) -> None:
    before = copy.deepcopy(original["payload"])
    after = copy.deepcopy(event["payload"])
    before_message = before.get("message", before)
    after_message = after.get("message", after)
    if not isinstance(before_message, dict) or not isinstance(after_message, dict):
        raise SurfaceError("invalid tool result message format")
    before_message["content"] = None
    after_message["content"] = None
    if before != after:
        raise SurfaceError("tool/result replacement may only modify content")


def fold_surface(events: List[Dict[str, object]]) -> Dict[str, object]:
    nodes: List[int] = []
    replacements: List[Dict[str, object]] = []
    for expected_seq, event in enumerate(events):
        if event.get("seq") != expected_seq:
            raise SurfaceError(f"surface event seq is not contiguous: expected {expected_seq}")
        if event["type"] not in SURFACE_EVENT_TYPES:
            if "surfaceOp" in event or "sourceEventSeqs" in event:
                raise SurfaceError("non-message events must not carry surface metadata")
            continue
        operation = event.get("surfaceOp", "append")
        if operation == "append":
            _source_references(event, [])
            nodes.append(expected_seq)
            continue
        if not isinstance(operation, dict) or set(operation) != {"op", "startSeq", "endSeq"} or operation.get("op") != "replace":
            raise SurfaceError("invalid surfaceOp")
        start = operation["startSeq"]
        end = operation["endSeq"]
        if not _valid_seq(start) or not _valid_seq(end) or start >= expected_seq or end >= expected_seq:
            raise SurfaceError("replacement may only reference earlier events")
        if start not in nodes or end not in nodes:
            raise SurfaceError("replacement start and end nodes must still be on the current surface")
        start_index = nodes.index(start)
        end_index = nodes.index(end)
        if start_index > end_index:
            raise SurfaceError("replacement start must not be after its end")
        shadowed = nodes[start_index:end_index + 1]
        _source_references(event, shadowed)
        if event["type"] == "tool/result":
            if len(shadowed) != 1:
                raise SurfaceError("tool/result replacement may only replace one current node")
            original = events[shadowed[0]]
            if original["type"] != "tool/result":
                raise SurfaceError("tool/result replacement target type mismatch")
            _validate_tool_rewrite(event, original)
        if start_index == 0 and events[nodes[0]]["type"] == "system/message":
            if event["type"] != "system/message" or len(shadowed) != 1:
                raise SurfaceError("the first system prompt may only be replaced by a single system/message")
        nodes[start_index:end_index + 1] = [expected_seq]
        replacements.append(
            {
                "seq": expected_seq,
                "startSeq": start,
                "endSeq": end,
                "shadowedSeqs": shadowed,
            }
        )
    return {"nodes": nodes, "replacements": replacements}
