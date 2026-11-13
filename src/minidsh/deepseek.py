import base64
import copy
import hashlib
import json
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .cancellation import CancellationToken, RunCancelled
from .events import EventBus
from .files import DEFAULT_FILE_EXPIRY_SECONDS, DEFAULT_FILES_API_TIMEOUT_MS, FILES_BETA, DeepSeekFilesClient, FilesError
from .images import ImageAttachment, ImageError, assert_inline_budget, materialize_images
from .llm import Model, ModelResponse
from .plugins import Plugin, ServiceContext
from .retry import RetryPolicy


PUBLIC_BASE_URL = "https://api.deepseek.com/anthropic"
UPSTREAM_VERSION = "0.2.2-rc.1"
USER_AGENT = f"deepseek-harness/{UPSTREAM_VERSION} (+https://github.com/deepseek-ai/deepseek-harness)"
DEFAULT_MAX_TOKENS = 256_000
DEFAULT_STREAM_IDLE_TIMEOUT_MS = 300_000
DEFAULT_MODELS_TIMEOUT_MS = 5_000
DEFAULT_MODELS_MAX_AGE_MS = 600_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request: Request, *args: object) -> None:
        return None


class LlmError(RuntimeError):
    def __init__(
        self,
        message: str,
        code: str,
        status: Optional[int] = None,
        retry_after_ms: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after_ms = retry_after_ms


@dataclass
class DeepSeekConfig:
    api_key: str
    base_url: str
    model: str
    reasoning_effort: str
    max_tokens: int
    stream_idle_timeout_ms: int
    models_timeout_ms: int
    models_max_age_ms: int
    retry_policy: RetryPolicy
    files_timeout_ms: int
    file_expiry_seconds: int
    image_mode: str

    @classmethod
    def from_env(cls, retry_enabled: bool = True) -> "DeepSeekConfig":
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
        image_mode = os.environ.get("DEEPSEEK_IMAGE_MODE", "auto")
        if image_mode not in {"auto", "inline", "required"}:
            raise LlmError("DEEPSEEK_IMAGE_MODE 必须是 auto、inline 或 required", "INVALID_REQUEST")
        try:
            max_tokens = int(os.environ.get("DEEPSEEK_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
            timeout = int(os.environ.get("DEEPSEEK_STREAM_IDLE_TIMEOUT_MS", str(DEFAULT_STREAM_IDLE_TIMEOUT_MS)))
            models_timeout = int(os.environ.get("DEEPSEEK_MODELS_TIMEOUT_MS", str(DEFAULT_MODELS_TIMEOUT_MS)))
            models_max_age = int(os.environ.get("DEEPSEEK_MODELS_MAX_AGE_MS", str(DEFAULT_MODELS_MAX_AGE_MS)))
            files_timeout = int(os.environ.get("DEEPSEEK_FILES_API_TIMEOUT_MS", str(DEFAULT_FILES_API_TIMEOUT_MS)))
            file_expiry = int(os.environ.get("DEEPSEEK_FILE_EXPIRY_SECONDS", str(DEFAULT_FILE_EXPIRY_SECONDS)))
            retry_policy = RetryPolicy.from_env(retry_enabled)
        except ValueError as exc:
            raise LlmError(f"DeepSeek 数值或重试配置无效: {exc}", "INVALID_REQUEST") from exc
        if max_tokens <= 0 or timeout <= 0 or models_timeout <= 0 or models_max_age < 0 or files_timeout <= 0:
            raise LlmError("DeepSeek 容量和超时必须大于 0，列表缓存时间不能为负数", "INVALID_REQUEST")
        if not 3_600 <= file_expiry <= 2_592_000:
            raise LlmError("DeepSeek 文件生命周期必须在 3600 到 2592000 秒之间", "INVALID_REQUEST")
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
            reasoning_effort=effort,
            max_tokens=max_tokens,
            stream_idle_timeout_ms=timeout,
            models_timeout_ms=models_timeout,
            models_max_age_ms=models_max_age,
            retry_policy=retry_policy,
            files_timeout_ms=files_timeout,
            file_expiry_seconds=file_expiry,
            image_mode=image_mode,
        )

    @property
    def messages_url(self) -> str:
        return f"{self.base_url}/messages" if self.base_url.endswith("/v1") else f"{self.base_url}/v1/messages"

    @property
    def models_url(self) -> str:
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}/models"


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


def serialize_input_blocks(
    blocks: List[Dict[str, object]],
    images: Dict[str, ImageAttachment],
    file_ids: Optional[Dict[str, str]],
) -> List[Dict[str, object]]:
    content: List[Dict[str, object]] = []
    for block in blocks:
        if block.get("type") == "text":
            if block.get("text"):
                content.append({"type": "text", "text": block["text"]})
            continue
        if block.get("type") != "image":
            continue
        attachment = block.get("attachment", {})
        sha256 = str(attachment.get("sha256", "")) if isinstance(attachment, dict) else ""
        if sha256 not in images:
            raise LlmError("DeepSeek Messages 请求图片缺失", "INVALID_REQUEST")
        image = images[sha256]
        content.append({"type": "text", "text": f"[Image: {image.path.name}; sha256={image.sha256[:16]}]"})
        if file_ids is not None:
            if sha256 not in file_ids:
                raise LlmError("DeepSeek Messages 请求图片 file id 缺失", "INVALID_REQUEST")
            source = {"type": "file", "file_id": file_ids[sha256]}
        else:
            source = {
                "type": "base64",
                "media_type": image.media_type,
                "data": base64.b64encode(image.data).decode("ascii"),
            }
        content.append({"type": "image", "source": source})
    return content


def serialize_history(
    messages: List[Dict[str, object]],
    target_model: str,
    images: Optional[Dict[str, ImageAttachment]] = None,
    file_ids: Optional[Dict[str, str]] = None,
) -> List[Dict[str, object]]:
    wire: List[Dict[str, object]] = []
    resolved_images = images or {}
    for message in messages:
        role = str(message.get("role", ""))
        blocks = text_content(message.get("content", []))
        if role == "user":
            content = serialize_input_blocks(blocks, resolved_images, file_ids)
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
            result_content = serialize_input_blocks(blocks, resolved_images, file_ids)
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
    images: Optional[Dict[str, ImageAttachment]] = None,
    file_ids: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    effort = config.reasoning_effort
    body: Dict[str, object] = {
        "model": config.model,
        "stream": True,
        "messages": serialize_history(messages, config.model, images, file_ids),
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


def iter_sse(response: object, cancel_token: Optional[CancellationToken] = None) -> Iterator[Dict[str, object]]:
    event_name: Optional[str] = None
    data_lines: List[str] = []
    for raw_line in response:  # type: ignore[attr-defined]
        if cancel_token is not None:
            cancel_token.throw_if_cancelled()
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


def translate_events(
    events: Iterator[Dict[str, object]],
    model: str,
    on_event: Optional[Callable[[Dict[str, object]], None]] = None,
) -> ModelResponse:
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
            if on_event is not None and message.get("usage") is not None:
                on_event({"type": "usage", "usage": dict(usage)})
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
            if on_event is not None and block_type == "tool_use":
                on_event({"type": "block-start", "index": wire_index, "blockType": "tool-call", "id": block["id"], "name": block["name"]})
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
                if on_event is not None:
                    if delta_type == "text_delta":
                        on_event({"type": "text-delta", "index": wire_index, "text": str(delta.get("text", ""))})
                    elif delta_type == "thinking_delta":
                        on_event({"type": "reasoning-delta", "index": wire_index, "text": str(delta.get("thinking", ""))})
                    elif delta_type == "input_json_delta":
                        on_event(
                            {
                                "type": "tool-call-delta",
                                "index": wire_index,
                                "id": block["id"],
                                "name": block["name"],
                                "argumentsDelta": str(delta.get("partial_json", "")),
                            }
                        )
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
                if on_event is not None:
                    on_event({"type": "usage", "usage": dict(usage)})
            continue

        if finish_reason is None or open_blocks:
            raise LlmError("DeepSeek Messages message_stop 时内容尚未收敛", "MALFORMED_RESPONSE")
        if not blocks and finish_reason["kind"] == "stop":
            raise LlmError("DeepSeek Messages 返回空内容", "EMPTY_RESPONSE")
        if finish_reason["kind"] == "max-tokens":
            retained = [(block, replay) for block, replay in zip(blocks, replay_blocks) if block.get("type") != "tool-call"]
            blocks = [block for block, _ in retained]
            replay_blocks = [replay for _, replay in retained]
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


def retry_after_ms(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        seconds = float(value)
        return max(1, round(seconds * 1000)) if seconds > 0 else None
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delay = (parsed - datetime.now(timezone.utc)).total_seconds()
        return max(1, round(delay * 1000)) if delay > 0 else None


def messages_http_error(error: HTTPError) -> LlmError:
    try:
        detail = error.read().decode("utf-8", errors="replace")
    except OSError:
        detail = ""
    try:
        body = json.loads(detail) if detail else {}
        provider_error = body.get("error", {}) if isinstance(body, dict) else {}
        error_type = provider_error.get("type", "") if isinstance(provider_error, dict) else ""
        message = provider_error.get("message", "") if isinstance(provider_error, dict) else ""
    except json.JSONDecodeError:
        error_type = ""
        message = ""
    if error.code in {401, 403} or error_type in {"authentication_error", "permission_error"}:
        code = "AUTH"
    elif error.code == 402:
        code = "QUOTA"
    elif error.code == 429 or error_type == "rate_limit_error":
        code = "RATE_LIMIT"
    elif error.code in {400, 413} or error_type == "invalid_request_error":
        code = "INVALID_REQUEST"
    elif error.code >= 500 or error_type in {"api_error", "overloaded_error"}:
        code = "SERVER"
    else:
        code = f"HTTP_{error.code}"
    text = str(message or detail or error.reason)
    return LlmError(
        f"DeepSeek Messages HTTP {error.code}: {text}",
        code,
        status=error.code,
        retry_after_ms=retry_after_ms(error.headers.get("retry-after")),
    )


def parse_model_listing(body: object) -> Dict[str, object]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise LlmError('DeepSeek model listing has no "data" array', "INVALID_RESPONSE")
    entries: List[Dict[str, object]] = []
    seen = set()
    for row in body["data"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in seen:
            continue
        model_id = row["id"]
        seen.add(model_id)
        capabilities = row.get("api_capabilities", {})
        messages = capabilities.get("anthropic_messages") if isinstance(capabilities, dict) else None
        outputs = row.get("output_modalities", ["text"])
        inputs = row.get("input_modalities", ["text"])
        usable = isinstance(messages, dict) and isinstance(outputs, list) and "text" in outputs and isinstance(inputs, list)
        known_inputs = [item for item in inputs if item in {"text", "image"}] if isinstance(inputs, list) else []
        usable = usable and bool(known_inputs)
        facts: Dict[str, object] = {"inputModalities": known_inputs}
        for wire, local in (("context_window", "contextWindow"), ("max_output_tokens", "maxOutputTokens")):
            value = row.get(wire)
            if value is None:
                continue
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                facts[local] = value
            else:
                usable = False
        entry: Dict[str, object] = {
            "id": model_id,
            "capabilities": {"usable": usable, "facts": facts},
        }
        if isinstance(row.get("name"), str) and row["name"]:
            entry["name"] = row["name"]
        entries.append(entry)
    if not entries:
        raise LlmError("DeepSeek model listing contains no model entry", "INVALID_RESPONSE")
    return {"entries": entries, "skipped": len(body["data"]) - len(entries)}


class DeepSeekModel(Model):
    provider = "deepseek-official"

    @property
    def model_id(self) -> str:
        return os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")

    def __init__(
        self,
        events: Optional[EventBus] = None,
        retry_enabled: bool = True,
        listing_enabled: bool = True,
        files_enabled: bool = True,
        workspace: Optional[Path] = None,
        allow_outside: bool = False,
    ) -> None:
        self.user_id = uuid.uuid4().hex
        self.events = events
        self.retry_enabled = retry_enabled
        self.listing_enabled = listing_enabled
        self.files_enabled = files_enabled
        self.workspace = (workspace or Path.cwd()).resolve()
        self.allow_outside = allow_outside
        self._opener = build_opener(NoRedirect)
        self._listing_lock = threading.Lock()
        self._listing_cached_at = 0.0
        self._listing_cache: Optional[Dict[str, object]] = None
        self._file_cache: Dict[Tuple[str, str, str], Dict[str, object]] = {}
        self._file_lock = threading.Lock()

    def complete(
        self,
        system: str,
        messages: List[Dict[str, object]],
        tool_definitions: List[Dict[str, object]],
        session_id: Optional[str] = None,
        cancel_token: Optional[CancellationToken] = None,
        on_event: Optional[Callable[[Dict[str, object]], None]] = None,
    ) -> ModelResponse:
        config = DeepSeekConfig.from_env(self.retry_enabled)
        token = cancel_token or CancellationToken()
        images, file_ids = self._prepare_images(messages, config, token)
        request = self._messages_request(config, system, messages, tool_definitions, images, file_ids, session_id)
        retries = 0

        def emit(event: Dict[str, object]) -> None:
            if on_event is not None:
                on_event({**event, "attempt": retries + 1})

        while True:
            token.throw_if_cancelled()
            try:
                return self._request_once(request, config, token, emit if on_event is not None else None)
            except RunCancelled:
                raise
            except LlmError as exc:
                if file_ids is not None and config.image_mode == "auto" and exc.code == "INVALID_REQUEST":
                    stale_ids = set(file_ids.values())
                    with self._file_lock:
                        for key, cached in list(self._file_cache.items()):
                            if cached["id"] in stale_ids:
                                del self._file_cache[key]
                    if self.events is not None:
                        self.events.emit("llm/image-fallback", {"reason": exc.code, "run_id": token.run_id, "session_id": session_id})
                    file_ids = None
                    assert_inline_budget(messages, images)
                    request = self._messages_request(config, system, messages, tool_definitions, images, None, session_id)
                    continue
                if not config.retry_policy.allows(exc.code, retries):
                    raise
                delay = config.retry_policy.delay_ms(retries, exc.retry_after_ms)
                retries += 1
                if self.events is not None:
                    self.events.emit(
                        "llm/retry",
                        {
                            "provider": self.provider,
                            "model": config.model,
                            "session_id": session_id,
                            "run_id": token.run_id,
                            "attempt": retries,
                            "delay_ms": delay,
                            "code": exc.code,
                        },
                    )
                if token.wait(delay / 1000):
                    raise RunCancelled("运行已取消")

    def _prepare_images(
        self,
        messages: List[Dict[str, object]],
        config: DeepSeekConfig,
        token: CancellationToken,
    ) -> Tuple[Dict[str, ImageAttachment], Optional[Dict[str, str]]]:
        try:
            images = materialize_images(messages, self.workspace, self.allow_outside)
        except ImageError as exc:
            raise LlmError(str(exc), "INVALID_REQUEST") from exc
        if not images:
            return images, None
        if config.image_mode == "inline" or not self.files_enabled:
            if config.image_mode == "required" and not self.files_enabled:
                raise LlmError("llm.files 被禁用，不能使用 required 图片模式", "INVALID_REQUEST")
            assert_inline_budget(messages, images)
            return images, None
        client = self._files_client(config)
        file_ids: Dict[str, str] = {}
        try:
            for sha256, image in images.items():
                token.throw_if_cancelled()
                credential_scope = hashlib.sha256(config.api_key.encode("utf-8")).hexdigest()
                cache_key = (config.base_url, credential_scope, sha256)
                file, uploaded = self._resolve_image_file(client, image, config, token, cache_key)
                file_ids[sha256] = str(file["id"])
                if self.events is not None:
                    self.events.emit(
                        "llm/image-upload",
                        {"sha256": sha256, "file_id": file["id"], "uploaded": uploaded, "run_id": token.run_id},
                    )
            return images, file_ids
        except FilesError as exc:
            if config.image_mode == "required":
                raise LlmError(str(exc), exc.code, exc.status) from exc
            assert_inline_budget(messages, images)
            if self.events is not None:
                self.events.emit("llm/image-fallback", {"reason": exc.code, "run_id": token.run_id})
            return images, None

    def _resolve_image_file(
        self,
        client: DeepSeekFilesClient,
        image: ImageAttachment,
        config: DeepSeekConfig,
        token: CancellationToken,
        cache_key: Tuple[str, str, str],
    ) -> Tuple[Dict[str, object], bool]:
        with self._file_lock:
            cached = self._file_cache.get(cache_key)
            if cached is not None and int(cached["expiresAt"]) > int(time.time()) + 3_600:
                return cached, False
            file = client.upload(image, config.file_expiry_seconds, token)
            self._file_cache[cache_key] = file
            return file, True

    def _files_client(self, config: DeepSeekConfig) -> DeepSeekFilesClient:
        return DeepSeekFilesClient(config.base_url, config.api_key, self.user_id, USER_AGENT, config.files_timeout_ms)

    def _messages_request(
        self,
        config: DeepSeekConfig,
        system: str,
        messages: List[Dict[str, object]],
        tool_definitions: List[Dict[str, object]],
        images: Dict[str, ImageAttachment],
        file_ids: Optional[Dict[str, str]],
        session_id: Optional[str],
    ) -> Request:
        body = serialize_request(config, system, messages, tool_definitions, images, file_ids)
        headers = {
            "content-type": "application/json",
            "accept": "text/event-stream",
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
            "user-agent": USER_AGENT,
            "x-deepseek-harness-user-id": self.user_id,
        }
        if file_ids:
            headers["anthropic-beta"] = FILES_BETA
        if session_id is not None:
            headers["x-deepseek-harness-session-id"] = session_id
        return Request(
            config.messages_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

    def list_files(self) -> Dict[str, object]:
        if not self.files_enabled:
            raise LlmError("llm.files 当前被禁用", "DISABLED")
        return self._files_client(DeepSeekConfig.from_env(self.retry_enabled)).list()

    def _request_once(
        self,
        request: Request,
        config: DeepSeekConfig,
        token: CancellationToken,
        on_event: Optional[Callable[[Dict[str, object]], None]] = None,
    ) -> ModelResponse:
        token.throw_if_cancelled()
        try:
            response = self._opener.open(request, timeout=config.stream_idle_timeout_ms / 1000)
        except HTTPError as exc:
            raise messages_http_error(exc) from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            token.throw_if_cancelled()
            is_timeout = isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(getattr(exc, "reason", None), socket.timeout)
            raise LlmError("DeepSeek Messages 请求超时" if is_timeout else "DeepSeek Messages 传输失败", "TIMEOUT" if is_timeout else "TRANSPORT") from exc
        unregister = token.on_cancel(response.close)
        try:
            with response:
                return translate_events(iter_sse(response, token), config.model, on_event)
        except RunCancelled:
            raise
        except ValueError as exc:
            token.throw_if_cancelled()
            raise exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            token.throw_if_cancelled()
            is_timeout = isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(getattr(exc, "reason", None), socket.timeout)
            raise LlmError("DeepSeek Messages 流空闲超时" if is_timeout else "DeepSeek Messages 流传输失败", "TIMEOUT" if is_timeout else "TRANSPORT") from exc
        finally:
            unregister()

    def list_models(self) -> Dict[str, object]:
        if not self.listing_enabled:
            raise LlmError("llm.model-listing 当前被禁用", "DISABLED")
        config = DeepSeekConfig.from_env(self.retry_enabled)
        with self._listing_lock:
            age_ms = (time.monotonic() - self._listing_cached_at) * 1000
            if self._listing_cache is not None and config.models_max_age_ms > 0 and age_ms < config.models_max_age_ms:
                return copy.deepcopy(self._listing_cache)
            request = Request(
                config.models_url,
                headers={
                    "accept": "application/json",
                    "x-api-key": config.api_key,
                    "anthropic-version": "2023-06-01",
                    "user-agent": USER_AGENT,
                    "x-deepseek-harness-user-id": self.user_id,
                },
                method="GET",
            )
            try:
                with self._opener.open(request, timeout=config.models_timeout_ms / 1000) as response:
                    raw = response.read(1_000_001)
            except HTTPError as exc:
                raise LlmError(f"{config.models_url} answered HTTP {exc.code}", f"HTTP_{exc.code}", status=exc.code) from exc
            except (URLError, TimeoutError, socket.timeout, OSError) as exc:
                is_timeout = isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(getattr(exc, "reason", None), socket.timeout)
                code = "TIMEOUT" if is_timeout else "TRANSPORT"
                raise LlmError(f"could not reach {config.models_url}", code) from exc
            if len(raw) > 1_000_000:
                raise LlmError("DeepSeek model listing response is too large", "INVALID_RESPONSE")
            try:
                body = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise LlmError(f"{config.models_url} did not answer with JSON", "INVALID_RESPONSE") from exc
            listing = parse_model_listing(body)
            self._listing_cache = copy.deepcopy(listing)
            self._listing_cached_at = time.monotonic()
            return listing


class DeepSeekModelPlugin(Plugin):
    name = "llm.deepseek"
    provides = ("llm",)

    def __init__(
        self,
        retry_enabled: bool = True,
        listing_enabled: bool = True,
        files_enabled: bool = True,
        workspace: Optional[Path] = None,
        allow_outside: bool = False,
    ) -> None:
        self.retry_enabled = retry_enabled
        self.listing_enabled = listing_enabled
        self.files_enabled = files_enabled
        self.workspace = workspace
        self.allow_outside = allow_outside

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide(
            "llm",
            DeepSeekModel(
                ctx.events,
                self.retry_enabled,
                self.listing_enabled,
                self.files_enabled,
                self.workspace,
                self.allow_outside,
            ),
            self.name,
        )
