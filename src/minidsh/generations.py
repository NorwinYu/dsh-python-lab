import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


MANIFEST_NAME = "index.json"
GENERATION_NAME = re.compile(r"generations/[0-9]{8}\.jsonl$")


class GenerationError(ValueError):
    pass


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def backup_original(root: Path, session_id: str, raw: bytes) -> str:
    backups = root / "backups"
    backups.mkdir(exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    name = f"{session_id}.{digest}.jsonl"
    target = backups / name
    if target.exists():
        if target.read_bytes() != raw:
            raise GenerationError("backup hash conflict")
    else:
        with target.open("xb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
    return f"backups/{name}"


def read_current(root: Path, session_id: str) -> Optional[Tuple[bytes, int]]:
    manifest_path = root / session_id / MANIFEST_NAME
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GenerationError(f"invalid session manifest: {session_id}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != 2
        or manifest.get("session_id") != session_id
        or not isinstance(manifest.get("generation"), int)
        or isinstance(manifest.get("generation"), bool)
        or manifest["generation"] < 1
        or not isinstance(manifest.get("path"), str)
        or not GENERATION_NAME.fullmatch(manifest["path"])
        or not isinstance(manifest.get("sha256"), str)
        or len(manifest["sha256"]) != 64
    ):
        raise GenerationError(f"invalid session manifest format: {session_id}")
    generation_path = root / session_id / manifest["path"]
    try:
        raw = generation_path.read_bytes()
    except OSError as exc:
        raise GenerationError(f"cannot read current session generation: {generation_path}") from exc
    if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise GenerationError(f"current session generation content hash mismatch: {generation_path}")
    return raw, int(manifest["generation"])


def publish(
    root: Path,
    session_id: str,
    events: List[Dict[str, object]],
) -> str:
    current = read_current(root, session_id)
    generation = current[1] + 1 if current is not None else 1
    parent = root / session_id
    parent.mkdir(parents=True, exist_ok=True)
    generations = parent / "generations"
    generations.mkdir(exist_ok=True)
    relative = f"generations/{generation:08d}.jsonl"
    target = parent / relative
    if target.exists():
        raise GenerationError(f"generation already exists; refusing to overwrite: {target}")
    header = {
        "record": "minidsh-session-header",
        "format": 2,
        "session_id": session_id,
        "generation": generation,
        "created_at": events[-1]["created_at"] if events else datetime.now(timezone.utc).isoformat(),
    }
    payload = bytearray((json.dumps(header, ensure_ascii=False) + "\n").encode("utf-8"))
    for event in events:
        payload.extend((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
    raw = bytes(payload)
    temporary = parent / f".{uuid.uuid4().hex}.generation"
    try:
        with temporary.open("xb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, target)
        manifest = {
            "format": 2,
            "session_id": session_id,
            "generation": generation,
            "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        manifest_temporary = parent / f".{uuid.uuid4().hex}.manifest"
        try:
            manifest_temporary.write_text(json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8")
            with manifest_temporary.open("rb") as file:
                os.fsync(file.fileno())
            os.replace(manifest_temporary, parent / MANIFEST_NAME)
            _fsync_directory(parent)
        finally:
            if manifest_temporary.exists():
                manifest_temporary.unlink()
        _fsync_directory(generations)
    finally:
        if temporary.exists():
            temporary.unlink()
    return relative
