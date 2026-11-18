import json
import socket
import uuid
from datetime import datetime
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .cancellation import CancellationToken
from .images import ImageAttachment


FILES_BETA = "files-api-2025-04-14"
DEFAULT_FILE_EXPIRY_SECONDS = 7 * 24 * 60 * 60
DEFAULT_FILES_API_TIMEOUT_MS = 60_000
MAX_FILE_UPLOAD_BYTES = 128 * 1024 * 1024


class FilesError(RuntimeError):
    def __init__(self, message: str, code: str = "FILES_API", status: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request: Request, *args: object) -> None:
        return None


def parse_file_object(value: object) -> Dict[str, object]:
    if not isinstance(value, dict):
        raise FilesError("DeepSeek Files API returned an invalid file object", "INVALID_RESPONSE")
    created = value.get("created_at")
    try:
        created_at = int(datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()) if isinstance(created, str) else -1
    except ValueError:
        created_at = -1
    if (
        not isinstance(value.get("id"), str)
        or not value["id"]
        or value.get("type") != "file"
        or not isinstance(value.get("size_bytes"), int)
        or isinstance(value.get("size_bytes"), bool)
        or value["size_bytes"] < 0
        or created_at < 0
        or not isinstance(value.get("filename"), str)
        or not value["filename"]
        or not isinstance(value.get("mime_type"), str)
    ):
        raise FilesError("DeepSeek Files API returned an invalid file object", "INVALID_RESPONSE")
    return {
        "id": value["id"],
        "bytes": value["size_bytes"],
        "createdAt": created_at,
        "filename": value["filename"],
        "mediaType": value["mime_type"],
    }


class DeepSeekFilesClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        user_id: str,
        user_agent: str,
        timeout_ms: int = DEFAULT_FILES_API_TIMEOUT_MS,
    ) -> None:
        root = base_url.rstrip("/")
        self.url = f"{root}/files" if root.endswith("/v1") else f"{root}/v1/files"
        self.api_key = api_key
        self.user_id = user_id
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self._opener = build_opener(NoRedirect)

    def upload(
        self,
        image: ImageAttachment,
        expires_after_seconds: int = DEFAULT_FILE_EXPIRY_SECONDS,
        cancel_token: Optional[CancellationToken] = None,
    ) -> Dict[str, object]:
        if image.size > MAX_FILE_UPLOAD_BYTES:
            raise FilesError("Files API files must not exceed 128 MiB", "INVALID_REQUEST")
        if not 3_600 <= expires_after_seconds <= 2_592_000:
            raise FilesError("Files API lifetime must be between 3600 and 2592000 seconds", "INVALID_REQUEST")
        boundary = f"minidsh-{uuid.uuid4().hex}"
        filename = f"dsh-{image.sha256[:16]}.{image.extension}"
        fields = {
            "expires_after[anchor]": "created_at",
            "expires_after[seconds]": str(expires_after_seconds),
        }
        body = bytearray()
        for name, value in fields.items():
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        body.extend(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: {image.media_type}\r\n\r\n".encode()
        )
        body.extend(image.data)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        result = self._request(
            "POST",
            self.url,
            bytes(body),
            f"multipart/form-data; boundary={boundary}",
            cancel_token,
        )
        file = parse_file_object(result)
        if file["bytes"] != image.size or file["mediaType"] != image.media_type:
            raise FilesError("Files API upload response size or type does not match the image", "INVALID_RESPONSE")
        file["expiresAt"] = int(file["createdAt"]) + expires_after_seconds
        return file

    def list(self, after: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, object]:
        query = []
        if after is not None:
            query.append(f"after_id={quote(after)}")
        if limit is not None:
            if not 1 <= limit <= 1000:
                raise FilesError("Files API limit must be between 1 and 1000", "INVALID_REQUEST")
            query.append(f"limit={limit}")
        suffix = "?" + "&".join(query) if query else ""
        result = self._request("GET", self.url + suffix)
        if not isinstance(result, dict) or not isinstance(result.get("data"), list) or not isinstance(result.get("has_more"), bool):
            raise FilesError("DeepSeek Files API returned an invalid list", "INVALID_RESPONSE")
        return {
            "data": [parse_file_object(item) for item in result["data"]],
            "firstId": result.get("first_id"),
            "lastId": result.get("last_id"),
            "hasMore": result["has_more"],
        }

    def retrieve(self, file_id: str) -> Dict[str, object]:
        return parse_file_object(self._request("GET", f"{self.url}/{quote(file_id, safe='')}"))

    def delete(self, file_id: str) -> None:
        result = self._request("DELETE", f"{self.url}/{quote(file_id, safe='')}")
        if not isinstance(result, dict) or result.get("id") != file_id or result.get("type") != "file_deleted":
            raise FilesError("DeepSeek Files API returned an invalid delete result", "INVALID_RESPONSE")

    def _request(
        self,
        method: str,
        url: str,
        data: Optional[bytes] = None,
        content_type: Optional[str] = None,
        cancel_token: Optional[CancellationToken] = None,
    ) -> object:
        token = cancel_token or CancellationToken()
        token.throw_if_cancelled()
        headers = {
            "accept": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": FILES_BETA,
            "user-agent": self.user_agent,
            "x-deepseek-harness-user-id": self.user_id,
        }
        if content_type is not None:
            headers["content-type"] = content_type
        request = Request(url, data=data, headers=headers, method=method)
        try:
            response = self._opener.open(request, timeout=self.timeout_ms / 1000)
        except HTTPError as exc:
            detail = exc.read(1_000_000)
            code = "AUTH" if exc.code in {401, 403} else "RATE_LIMIT" if exc.code == 429 else "SERVER" if exc.code >= 500 else "FILES_API"
            raise FilesError(f"DeepSeek Files API HTTP {exc.code}: {detail.decode(errors='replace')}", code, exc.code) from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            token.throw_if_cancelled()
            is_timeout = isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(getattr(exc, "reason", None), socket.timeout)
            raise FilesError("DeepSeek Files API request failed", "TIMEOUT" if is_timeout else "TRANSPORT") from exc
        unregister = token.on_cancel(response.close)
        try:
            with response:
                raw = response.read(1_000_001)
        except (URLError, TimeoutError, socket.timeout, OSError, ValueError) as exc:
            token.throw_if_cancelled()
            raise FilesError("DeepSeek Files API read failed", "TRANSPORT") from exc
        finally:
            unregister()
        if len(raw) > 1_000_000:
            raise FilesError("DeepSeek Files API response is too large", "INVALID_RESPONSE")
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FilesError("DeepSeek Files API returned invalid JSON", "INVALID_RESPONSE") from exc
