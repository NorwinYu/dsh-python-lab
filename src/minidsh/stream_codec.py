import copy
from typing import Dict, List


class StreamCodecError(ValueError):
    pass


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise StreamCodecError(f"{label} 必须是非负整数")
    return value


def _string_array(value: object, label: str) -> List[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise StreamCodecError(f"{label} 必须是非空字符串数组")
    return value


def pack_stream(chunks: List[Dict[str, object]]) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []
    for value in chunks:
        chunk = dict(value)
        time = _integer(chunk.get("time"), "chunk.time")
        chunk_type = chunk.get("type")
        if chunk_type in {"text-delta", "reasoning-delta"}:
            index = _integer(chunk.get("index"), "chunk.index")
            text = chunk.get("text")
            if not isinstance(text, str):
                raise StreamCodecError("文本增量必须是字符串")
            record_type = "text-chunks" if chunk_type == "text-delta" else "reasoning-chunks"
            previous = records[-1] if records else None
            if previous and previous["type"] == record_type and previous["index"] == index:
                previous["dt"].append(time - previous["lastTime"])
                previous["texts"].append(text)
                previous["lastTime"] = time
            else:
                records.append({"type": record_type, "time0": time, "index": index, "dt": [], "texts": [text], "lastTime": time})
        elif chunk_type == "tool-call-delta":
            index = _integer(chunk.get("index"), "chunk.index")
            call_id = chunk.get("id")
            name = chunk.get("name")
            arguments = chunk.get("argumentsDelta")
            if not isinstance(call_id, str) or not call_id or not isinstance(arguments, str):
                raise StreamCodecError("工具参数增量需要 id 和 argumentsDelta")
            if name is not None and not isinstance(name, str):
                raise StreamCodecError("工具名称必须是字符串")
            previous = records[-1] if records else None
            if (
                previous
                and previous["type"] == "tool-call-chunks"
                and previous["index"] == index
                and previous["id"] == call_id
                and previous.get("name") == name
            ):
                previous["dt"].append(time - previous["lastTime"])
                previous["args"].append(arguments)
                previous["lastTime"] = time
            else:
                record = {"type": "tool-call-chunks", "time0": time, "index": index, "dt": [], "id": call_id, "args": [arguments], "lastTime": time}
                if name is not None:
                    record["name"] = name
                records.append(record)
        elif chunk_type in {"block-start", "usage", "finish"}:
            allowed = {"type", "index", "blockType", "id", "name", "usage", "reason", "replayState"}
            records.append({"type": "chunk", "time": time, "chunk": {key: copy.deepcopy(item) for key, item in chunk.items() if key in allowed}})
        else:
            raise StreamCodecError(f"不支持的流事件: {chunk_type}")
    for record in records:
        record.pop("lastTime", None)
    return copy.deepcopy(records)


def expand_stream(records: object) -> List[Dict[str, object]]:
    if not isinstance(records, list):
        raise StreamCodecError("stream 必须是记录数组")
    chunks: List[Dict[str, object]] = []
    for value in records:
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise StreamCodecError("stream record 必须是带类型的对象")
        record_type = value["type"]
        if record_type == "chunk":
            if set(value) != {"type", "time", "chunk"} or not isinstance(value["chunk"], dict):
                raise StreamCodecError("raw chunk record 格式无效")
            chunks.append({"time": _integer(value["time"], "record.time"), **copy.deepcopy(value["chunk"])})
            continue
        if record_type not in {"text-chunks", "reasoning-chunks", "tool-call-chunks"}:
            raise StreamCodecError(f"不支持的 stream record: {record_type}")
        time0 = _integer(value.get("time0"), "record.time0")
        index = _integer(value.get("index"), "record.index")
        dt = value.get("dt")
        if not isinstance(dt, list) or any(not isinstance(item, int) or isinstance(item, bool) for item in dt):
            raise StreamCodecError("record.dt 必须是整数数组")
        if record_type == "tool-call-chunks":
            members = _string_array(value.get("args"), "record.args")
            call_id = value.get("id")
            if not isinstance(call_id, str) or not call_id:
                raise StreamCodecError("工具 run 需要非空 id")
        else:
            members = _string_array(value.get("texts"), "record.texts")
        if len(dt) != len(members) - 1:
            raise StreamCodecError("record.dt 长度必须比成员数少 1")
        current = time0
        for member_index, member in enumerate(members):
            if member_index:
                current += dt[member_index - 1]
            if record_type == "tool-call-chunks":
                chunk = {
                    "type": "tool-call-delta",
                    "time": current,
                    "index": index,
                    "id": call_id,
                    "argumentsDelta": member,
                }
                if "name" in value:
                    chunk["name"] = value["name"]
            else:
                chunk = {
                    "type": "text-delta" if record_type == "text-chunks" else "reasoning-delta",
                    "time": current,
                    "index": index,
                    "text": member,
                }
            chunks.append(chunk)
    return chunks


def stream_text(records: object) -> str:
    return "".join(chunk["text"] for chunk in expand_stream(records) if chunk["type"] == "text-delta")
