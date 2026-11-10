import json
import os
import uuid
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .llm import Model, ModelResponse
from .plugins import Plugin, ServiceContext


PUBLIC_BASE_URL = "https://api.deepseek.com/anthropic"
UPSTREAM_VERSION = "0.2.2-rc.1"
USER_AGENT = f"deepseek-harness/{UPSTREAM_VERSION} (+https://github.com/deepseek-ai/deepseek-harness)"
DEFAULT_MAX_TOKENS = 256_000
DEFAULT_STREAM_IDLE_TIMEOUT_MS = 300_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request: Request, *args: object) -> None:
        return None


class LlmError(RuntimeError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class DeepSeekConfig:
    api_key: str
    base_url: str
    model: str
    reasoning_effort: str
    max_tokens: int
    stream_idle_timeout_ms: int

    @classmethod
    def from_env(cls) -> "DeepSeekConfig":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise LlmError("缺少 DEEPSEEK_API_KEY；不会读取或写入其他密钥文件", "MISSING_CREDENTIAL")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", PUBLIC_BASE_URL).rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise LlmError("DEEPSEEK_BASE_URL 必须是无凭据、查询或片段的 HTTP(S) 根地址", "INVALID_REQUEST")
        effort = os.environ.get("DEEPSEEK_REASONING_EFFORT", "high")
        if effort not in {"off", "low", "high", "max"}:
            raise LlmError(f"不支持的 reasoning effort: {effort}", "UNSUPPORTED_REASONING_EFFORT")
        try:
            max_tokens = int(os.environ.get("DEEPSEEK_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
            timeout = int(os.environ.get("DEEPSEEK_STREAM_IDLE_TIMEOUT_MS", str(DEFAULT_STREAM_IDLE_TIMEOUT_MS)))
        except ValueError as exc:
            raise LlmError("DeepSeek 数值配置必须是整数", "INVALID_REQUEST") from exc
        if max_tokens <= 0 or timeout <= 0:
            raise LlmError("DeepSeek max tokens 和超时必须大于 0", "INVALID_REQUEST")
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
            reasoning_effort=effort,
            max_tokens=max_tokens,
            stream_idle_timeout_ms=timeout,
        )

    @property
    def messages_url(self) -> str:
        return f"{self.base_url}/messages" if self.base_url.endswith("/v1") else f"{self.base_url}/v1/messages"


def text_content(content: object) -> List[Dict[str, object]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        raise LlmError("消息 content 必须是字符串或内容块列表", "INVALID_REQUEST")
    return [dict(block) for block in content if isinstance(block, dict)]


def replay_signatures(message: Dict[str, object], target_model: str) -> Dict[int, str]:
    source = message.get("source", {})
    if not isinstance(source, dict):
        return {}
    replay = source.get("replayState", {})
    if not isinstance(replay, dict):
        return {}
    response = replay.get("response", {})
    if not isinstance(response, dict) or response.get("model") != target_model or source.get("model") != target_model:
        return {}
    blocks = replay.get("blocks", [])
    if not isinstance(blocks, list):
        return {}
    signatures: Dict[int, str] = {}
    for index, block in enumerate(blocks):
        if isinstance(block, dict) and isinstance(block.get("signature"), str):
            signatures[index] = block["signature"]
    return signatures


def serialize_history(messages: List[Dict[str, object]], target_model: str) -> List[Dict[str, object]]:
    wire: List[Dict[str, object]] = []
    for message in messages:
        role = str(message.get("role", ""))
        blocks = text_content(message.get("content", []))
        if role == "user":
            content = [block for block in blocks if block.get("type") == "text" and block.get("text")]
            wire_role = "user"
        elif role == "assistant":
            content = []
            signatures = replay_signatures(message, target_model)
            for index, block in enumerate(blocks):
                block_type = block.get("type")
                if block_type == "text" and block.get("text"):
                    content.append({"type": "text", "text": block["text"]})
                elif block_type == "reasoning":
                    item: Dict[str, object] = {"type": "thinking", "thinking": block.get("text", "")}
                    if index in signatures:
                        item["signature"] = signatures[index]
                    content.append(item)
                elif block_type == "tool-call":
                    arguments = block.get("arguments", {})
                    if not isinstance(arguments, dict):
                        arguments = {}
                    content.append(
                        {
                            "type": "tool_use",
                            "id": block["id"],
                            "name": block["name"],
                            "input": arguments,
                        }
                    )
            wire_role = "assistant"
        elif role == "tool":
            result_content = [block for block in blocks if block.get("type") == "text"]
            item = {
                "type": "tool_result",
                "tool_use_id": message.get("toolCallId", message.get("tool_call_id")),
                "content": result_content,
            }
            if message.get("isError") is True:
                item["is_error"] = True
            content = [item]
            wire_role = "user"
        else:
            raise LlmError(f"DeepSeek Messages 不支持历史角色: {role}", "INVALID_REQUEST")

        if not content:
            continue
        if wire and wire[-1]["role"] == wire_role:
            previous_content = wire[-1]["content"]
            assert isinstance(previous_content, list)
            previous_content.extend(content)
        else:
            wire.append({"role": wire_role, "content": content})
    return wire


def serialize_request(
    config: DeepSeekConfig,
    system: str,
    messages: List[Dict[str, object]],
    tool_definitions: List[Dict[str, object]],
) -> Dict[str, object]:
    effort = config.reasoning_effort
    body: Dict[str, object] = {
        "model": config.model,
        "stream": True,
        "messages": serialize_history(messages, config.model),
        "max_tokens": config.max_tokens,
        "thinking": {"type": "disabled" if effort == "off" else "enabled"},
        "tools": [
            {
                "name": definition["name"],
                "description": definition["description"],
                "input_schema": definition["parameters"],
            }
            for definition in tool_definitions
        ],
    }
    if effort != "off":
        body["output_config"] = {"effort": effort}
    if system:
        body["system"] = system
    return body


def iter_sse(response: object) -> Iterator[Dict[str, object]]:
    event_name: Optional[str] = None
    data_lines: List[str] = []
    for raw_line in response:  # type: ignore[attr-defined]
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            if not data_lines:
                event_name = None
                continue
            data = "\n".join(data_lines)
            try:
                event = json.loads(data)
            except json.JSONDecodeError as exc:
                raise LlmError("DeepSeek Messages SSE 包含无效 JSON", "MALFORMED_RESPONSE") from exc
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise LlmError("DeepSeek Messages SSE 事件缺少类型", "MALFORMED_RESPONSE")
            if event_name is not None and event_name != event["type"]:
                raise LlmError("DeepSeek Messages SSE 事件类型不一致", "MALFORMED_RESPONSE")
            yield event
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        raise LlmError("DeepSeek Messages SSE 以不完整事件结束", "STREAM_CLOSED")


def require_object(value: object) -> Dict[str, object]:
    if not isinstance(value, dict):
        raise LlmError("DeepSeek Messages 需要 JSON 对象", "MALFORMED_RESPONSE")
    return value


def update_usage(usage: Dict[str, int], raw: object) -> None:
    fields = require_object(raw)
    names = {
        "input_tokens": "inputTokens",
        "output_tokens": "outputTokens",
        "cache_read_input_tokens": "cacheReadTokens",
        "cache_creation_input_tokens": "cacheWriteTokens",
    }
    for wire, local in names.items():
        value = fields.get(wire)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise LlmError(f"DeepSeek Messages usage 字段无效: {wire}", "MALFORMED_RESPONSE")
        usage[local] = value


def translate_events(events: Iterator[Dict[str, object]], model: str) -> ModelResponse:
    blocks: List[Dict[str, object]] = []
    replay_blocks: List[Dict[str, object]] = []
    open_blocks: Dict[int, Dict[str, object]] = {}
    usage: Dict[str, int] = {}
    finish_reason: Optional[Dict[str, object]] = None
    started = False

    for event in events:
        event_type = event["type"]
        if event_type == "message_start":
            if started:
                raise LlmError("DeepSeek Messages 重复 message_start", "MALFORMED_RESPONSE")
            message = require_object(event.get("message"))
            update_usage(usage, message.get("usage", {}))
            started = True
            continue
        if event_type not in {"content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"}:
            continue
        if not started:
            raise LlmError("DeepSeek Messages 内容事件早于 message_start", "MALFORMED_RESPONSE")

        if event_type == "content_block_start":
            wire_index = event.get("index")
            if not isinstance(wire_index, int) or wire_index < 0 or wire_index in open_blocks:
                raise LlmError("DeepSeek Messages 内容块索引无效", "MALFORMED_RESPONSE")
            native = require_object(event.get("content_block"))
            block_type = native.get("type")
            if block_type == "text":
                block = {"type": "text", "text": native.get("text", "")}
                replay = {"type": "text"}
            elif block_type == "thinking":
                block = {"type": "reasoning", "text": native.get("thinking", "")}
                replay = {"type": "reasoning"}
                if isinstance(native.get("signature"), str):
                    replay["signature"] = native["signature"]
            elif block_type == "tool_use":
                arguments = native.get("input", {})
                block = {
                    "type": "tool-call",
                    "id": native.get("id", ""),
                    "name": native.get("name", ""),
                    "arguments": arguments if isinstance(arguments, dict) else {},
                }
                replay = {"type": "tool-call"}
            else:
                raise LlmError(f"DeepSeek Messages 不支持响应块: {block_type}", "UNSUPPORTED_CONTENT")
            block["_json"] = ""
            block["_order"] = len(blocks) + len(open_blocks)
            open_blocks[wire_index] = {"block": block, "replay": replay}
            continue

        if event_type in {"content_block_delta", "content_block_stop"}:
            wire_index = event.get("index")
            if not isinstance(wire_index, int) or wire_index not in open_blocks:
                raise LlmError("DeepSeek Messages 内容块 delta/stop 无对应起点", "MALFORMED_RESPONSE")
            entry = open_blocks[wire_index]
            block = entry["block"]
            replay = entry["replay"]
            if event_type == "content_block_delta":
                delta = require_object(event.get("delta"))
                delta_type = delta.get("type")
                if delta_type == "text_delta" and block.get("type") == "text":
                    block["text"] = str(block.get("text", "")) + str(delta.get("text", ""))
                elif delta_type == "thinking_delta" and block.get("type") == "reasoning":
                    block["text"] = str(block.get("text", "")) + str(delta.get("thinking", ""))
                elif delta_type == "signature_delta" and block.get("type") == "reasoning":
                    replay["signature"] = str(replay.get("signature", "")) + str(delta.get("signature", ""))
                elif delta_type == "input_json_delta" and block.get("type") == "tool-call":
                    block["_json"] = str(block.get("_json", "")) + str(delta.get("partial_json", ""))
                else:
                    raise LlmError(f"DeepSeek Messages 不支持 delta: {delta_type}", "MALFORMED_RESPONSE")
            else:
                if block.get("type") == "tool-call" and block.get("_json"):
                    try:
                        block["arguments"] = json.loads(str(block["_json"]))
                    except json.JSONDecodeError as exc:
                        raise LlmError("DeepSeek Messages 工具参数不是有效 JSON", "MALFORMED_RESPONSE") from exc
                block.pop("_json", None)
                blocks.append(block)
                replay_blocks.append(replay)
                del open_blocks[wire_index]
            continue

        if event_type == "message_delta":
            delta = require_object(event.get("delta"))
            reason = delta.get("stop_reason")
            if reason is not None:
                reasons = {
                    "end_turn": {"kind": "stop"},
                    "stop_sequence": {"kind": "stop"},
                    "tool_use": {"kind": "tool-calls"},
                    "max_tokens": {"kind": "max-tokens"},
                }
                if reason not in reasons:
                    raise LlmError(f"DeepSeek Messages 不支持 stop reason: {reason}", "MALFORMED_RESPONSE")
                finish_reason = reasons[reason]
            if "usage" in event:
                update_usage(usage, event["usage"])
            continue

        if finish_reason is None or open_blocks:
            raise LlmError("DeepSeek Messages message_stop 时内容尚未收敛", "MALFORMED_RESPONSE")
        if not blocks and finish_reason["kind"] == "stop":
            raise LlmError("DeepSeek Messages 返回空内容", "EMPTY_RESPONSE")
        usage["totalTokens"] = sum(usage.values())
        ordered = sorted(zip(blocks, replay_blocks), key=lambda pair: pair[0]["_order"])
        ordered_blocks = []
        ordered_replay = []
        for block, replay in ordered:
            block.pop("_order", None)
            ordered_blocks.append(block)
            ordered_replay.append(replay)
        return ModelResponse(
            blocks=ordered_blocks,
            finish_reason=finish_reason,
            usage=usage,
            replay_state={
                "response": {"kind": "deepseek-messages", "version": 1, "model": model},
                "blocks": ordered_replay,
            },
        )

    raise LlmError("DeepSeek Messages 在 message_stop 前关闭", "STREAM_CLOSED")


class DeepSeekModel(Model):
    provider = "deepseek-official"

    @property
    def model_id(self) -> str:
        return os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")

    def __init__(self) -> None:
        self.user_id = uuid.uuid4().hex

    def complete(
        self,
        system: str,
        messages: List[Dict[str, object]],
        tool_definitions: List[Dict[str, object]],
        session_id: Optional[str] = None,
    ) -> ModelResponse:
        config = DeepSeekConfig.from_env()
        body = serialize_request(config, system, messages, tool_definitions)
        headers = {
            "content-type": "application/json",
            "accept": "text/event-stream",
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
            "user-agent": USER_AGENT,
            "x-deepseek-harness-user-id": self.user_id,
        }
        if session_id is not None:
            headers["x-deepseek-harness-session-id"] = session_id
        request = Request(
            config.messages_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with build_opener(NoRedirect).open(request, timeout=config.stream_idle_timeout_ms / 1000) as response:
                return translate_events(iter_sse(response), config.model)
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except OSError:
                detail = ""
            raise LlmError(f"DeepSeek Messages HTTP {exc.code}: {detail}", "TRANSPORT") from exc


class DeepSeekModelPlugin(Plugin):
    name = "llm.deepseek"
    provides = ("llm",)

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("llm", DeepSeekModel(), self.name)
