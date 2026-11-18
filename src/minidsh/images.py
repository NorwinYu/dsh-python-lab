import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List


MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_REQUEST_IMAGE_BYTES = 128 * 1024 * 1024
MAX_INLINE_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES_PER_REQUEST = 600


class ImageError(ValueError):
    pass


@dataclass(frozen=True)
class ImageAttachment:
    path: Path
    media_type: str
    sha256: str
    data: bytes = field(repr=False)

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def extension(self) -> str:
        return {"image/png": "png", "image/jpeg": "jpeg", "image/gif": "gif", "image/webp": "webp"}[self.media_type]

    def block(self) -> Dict[str, object]:
        return {
            "type": "image",
            "attachment": {
                "path": str(self.path),
                "mediaType": self.media_type,
                "sha256": self.sha256,
                "bytes": self.size,
            },
        }


def resolve_image_path(workspace: Path, value: object, allow_outside: bool = False) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ImageError("image path must be a non-empty string")
    path = (workspace / Path(value)).expanduser().resolve()
    if not allow_outside:
        try:
            path.relative_to(workspace.resolve())
        except ValueError as exc:
            raise ImageError("image path must not leave the current workspace") from exc
    return path


def media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ImageError("only valid PNG, JPEG, GIF, and WebP images are supported")


def load_image(workspace: Path, value: object, allow_outside: bool = False) -> ImageAttachment:
    path = resolve_image_path(workspace, value, allow_outside)
    try:
        if not path.is_file():
            raise ImageError(f"image is not a regular file: {path}")
        size = path.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise ImageError("a single image must not exceed 32 MiB")
        data = path.read_bytes()
    except OSError as exc:
        raise ImageError(f"cannot read image {path}: {exc}") from exc
    if len(data) != size:
        raise ImageError(f"image changed while it was being read: {path}")
    return ImageAttachment(path, media_type(data), hashlib.sha256(data).hexdigest(), data)


def load_image_blocks(workspace: Path, paths: Iterable[str], allow_outside: bool = False) -> List[Dict[str, object]]:
    return [load_image(workspace, path, allow_outside).block() for path in paths]


def materialize_images(
    messages: List[Dict[str, object]],
    workspace: Path,
    allow_outside: bool = False,
) -> Dict[str, ImageAttachment]:
    images: Dict[str, ImageAttachment] = {}
    total = 0
    occurrences = 0
    for message in messages:
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            if message.get("role") not in {"user", "tool"}:
                raise ImageError("DeepSeek Messages only accepts images from user or tool result messages")
            attachment = block.get("attachment")
            if not isinstance(attachment, dict):
                raise ImageError("image content block is missing attachment")
            image = load_image(workspace, attachment.get("path"), allow_outside)
            if image.sha256 != attachment.get("sha256") or image.size != attachment.get("bytes"):
                raise ImageError(f"image changed since it was added to the session: {image.path}")
            if image.media_type != attachment.get("mediaType"):
                raise ImageError(f"image media type changed: {image.path}")
            images[image.sha256] = image
            total += image.size
            occurrences += 1
    if occurrences > MAX_IMAGES_PER_REQUEST or total > MAX_REQUEST_IMAGE_BYTES:
        raise ImageError("at most 600 images are allowed, with a combined raw size of at most 128 MiB")
    return images


def assert_inline_budget(messages: List[Dict[str, object]], images: Dict[str, ImageAttachment]) -> None:
    encoded_size = 0
    for message in messages:
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            attachment = block.get("attachment", {})
            if isinstance(attachment, dict):
                image = images[str(attachment.get("sha256"))]
                encoded_size += ((image.size + 2) // 3) * 4
    if encoded_size > MAX_INLINE_IMAGE_BYTES:
        raise ImageError("total inline image base64 size must not exceed 20 MiB")
