import errno
import fcntl
import hashlib
import json
import os
import re
import threading
import uuid
from abc import ABC, abstractmethod
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import ContextManager, Dict, List, Optional

from .plugins import Plugin, ServiceContext
from .session_recovery import open_turn_closers


SESSION_FORMAT_VERSION = 1
SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
HEADER_RECORD = "minidsh-session-header"


class SessionCorruptionError(ValueError):
    pass


class SessionFormatUnsupportedError(ValueError):
    pass


class SessionAlreadyOwnedError(RuntimeError):
    pass


class SessionStore(ABC):
    @abstractmethod
    def append(self, session_id: str, event_type: str, payload: Dict[str, object]) -> None:
        pass

    @abstractmethod
    def load(self, session_id: str) -> List[Dict[str, object]]:
        pass

    @abstractmethod
    def fork(self, session_id: str, at_seq: Optional[int] = None) -> str:
        pass

    @abstractmethod
    def derive_messages(self, session_id: str) -> List[Dict[str, object]]:
        pass

    @abstractmethod
    def derive_system(self, session_id: str) -> str:
        pass

    @abstractmethod
    def latest_turn(self, session_id: str) -> int:
        pass

    @abstractmethod
    def list_sessions(self) -> List[Dict[str, object]]:
        pass

    @abstractmethod
    def transcript(self, session_id: str) -> str:
        pass

    def write_lease(self, session_id: str) -> ContextManager[object]:
        return nullcontext()

    def repair(self, session_id: str) -> Dict[str, object]:
        raise RuntimeError("当前会话存储不支持恢复")

    def migrate(self, session_id: str) -> Dict[str, object]:
        raise RuntimeError("当前会话存储不支持迁移")

    def migrate_all(self) -> List[Dict[str, object]]:
        raise RuntimeError("当前会话存储不支持迁移")


@dataclass
class ReadResult:
    events: List[Dict[str, object]]
    version: int
    torn: bool
    raw: bytes


class SessionWriteLease:
    def __init__(self, store: "JsonlSessionStore", session_id: str) -> None:
        self.store = store
        self.session_id = session_id
        self.active = False

    def __enter__(self) -> "SessionWriteLease":
        self.store._acquire(self.session_id)
        self.active = True
        return self

    def __exit__(self, *args: object) -> None:
        if self.active:
            self.store._release(self.session_id)
            self.active = False


class JsonlSessionStore(SessionStore):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._leases: Dict[str, Dict[str, object]] = {}

    def _path(self, session_id: str) -> Path:
        if not SESSION_ID.fullmatch(session_id):
            raise ValueError("无效 session id")
        return self.root / f"{session_id}.jsonl"

    def _lock_path(self, session_id: str) -> Path:
        return self.root / ".locks" / f"{session_id}.lock"

    def write_lease(self, session_id: str) -> SessionWriteLease:
        self._path(session_id)
        return SessionWriteLease(self, session_id)

    def _acquire(self, session_id: str) -> None:
        thread_id = threading.get_ident()
        with self._lock:
            held = self._leases.get(session_id)
            if held is not None:
                if held["thread"] != thread_id:
                    raise SessionAlreadyOwnedError(f"会话正在被另一个写入者使用: {session_id}")
                held["depth"] = int(held["depth"]) + 1
                return
            path = self._lock_path(session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+b")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                if exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    raise SessionAlreadyOwnedError(f"会话已被其他进程锁定: {session_id}") from exc
                raise
            held_stat = os.fstat(handle.fileno())
            current_stat = path.stat()
            if (held_stat.st_dev, held_stat.st_ino) != (current_stat.st_dev, current_stat.st_ino):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
                raise SessionAlreadyOwnedError(f"会话锁路径发生变化，请重试: {session_id}")
            self._leases[session_id] = {"thread": thread_id, "depth": 1, "handle": handle}

    def _release(self, session_id: str) -> None:
        thread_id = threading.get_ident()
        with self._lock:
            held = self._leases.get(session_id)
            if held is None or held["thread"] != thread_id:
                raise RuntimeError("会话写锁不属于当前线程")
            depth = int(held["depth"]) - 1
            if depth > 0:
                held["depth"] = depth
                return
            handle = held["handle"]
            del self._leases[session_id]
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _read(self, session_id: str, allow_torn: bool = False) -> ReadResult:
        path = self._path(session_id)
        if not path.exists():
            return ReadResult([], SESSION_FORMAT_VERSION, False, b"")
        raw = path.read_bytes()
        if not raw:
            raise SessionCorruptionError(f"会话文件为空: {session_id}")
        torn = not raw.endswith(b"\n")
        if torn and not allow_torn:
            raise SessionCorruptionError(f"会话末尾存在不完整记录，请先 repair: {session_id}")
        committed = raw
        if torn:
            last_newline = raw.rfind(b"\n")
            if last_newline < 0:
                raise SessionCorruptionError(f"会话没有可恢复的完整记录: {session_id}")
            committed = raw[: last_newline + 1]
        try:
            lines = committed.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise SessionCorruptionError(f"会话不是有效 UTF-8: {session_id}") from exc
        if not lines:
            raise SessionCorruptionError(f"会话没有版本头或事件: {session_id}")
        try:
            first = json.loads(lines[0])
        except json.JSONDecodeError as exc:
            raise SessionCorruptionError(f"会话首行无效: {session_id}") from exc
        version = 0
        event_lines = lines
        if isinstance(first, dict) and first.get("record") == HEADER_RECORD:
            version = first.get("format")
            if not isinstance(version, int) or isinstance(version, bool):
                raise SessionCorruptionError("会话版本头格式无效")
            if version > SESSION_FORMAT_VERSION:
                raise SessionFormatUnsupportedError(f"不支持的未来会话格式: v{version}")
            if version != SESSION_FORMAT_VERSION:
                raise SessionFormatUnsupportedError(f"不支持的会话版本头: v{version}")
            if first.get("session_id") != session_id:
                raise SessionCorruptionError("会话版本头与文件名不一致")
            event_lines = lines[1:]
        events: List[Dict[str, object]] = []
        for index, line in enumerate(event_lines):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SessionCorruptionError(f"会话第 {index + 1} 个事件 JSON 无效") from exc
            self._validate_event(event, session_id, index)
            events.append(event)
        return ReadResult(events, version, torn, raw)

    @staticmethod
    def _validate_event(event: object, session_id: str, sequence: int) -> None:
        if not isinstance(event, dict):
            raise SessionCorruptionError("会话事件必须是对象")
        if event.get("session_id") != session_id:
            raise SessionCorruptionError("会话事件身份不一致")
        if event.get("seq") != sequence or isinstance(event.get("seq"), bool):
            raise SessionCorruptionError(f"会话事件 seq 不连续: 预期 {sequence}")
        if not isinstance(event.get("type"), str) or not event["type"]:
            raise SessionCorruptionError("会话事件类型无效")
        if not isinstance(event.get("payload"), dict):
            raise SessionCorruptionError("会话事件 payload 必须是对象")
        if not isinstance(event.get("created_at"), str):
            raise SessionCorruptionError("会话事件时间无效")

    def load(self, session_id: str) -> List[Dict[str, object]]:
        with self._lock:
            return self._read(session_id).events

    def _append_current(
        self,
        session_id: str,
        event_type: str,
        payload: Dict[str, object],
        created_at: Optional[str] = None,
    ) -> None:
        path = self._path(session_id)
        events = self._read(session_id).events if path.exists() else []
        if not path.exists():
            self._write_atomic(session_id, [], None)
        event = {
            "session_id": session_id,
            "seq": len(events),
            "type": event_type,
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())

    def append(self, session_id: str, event_type: str, payload: Dict[str, object]) -> None:
        with self._lock:
            if session_id in self._leases:
                self._append_ready(session_id, event_type, payload)
                return
            with self.write_lease(session_id):
                self._append_ready(session_id, event_type, payload)

    def _append_ready(self, session_id: str, event_type: str, payload: Dict[str, object]) -> None:
        path = self._path(session_id)
        if path.exists():
            stored = self._read(session_id)
            if stored.torn:
                raise SessionCorruptionError("写入前必须先修复不完整末尾")
            if stored.version == 0:
                backup = self._write_atomic(session_id, stored.events, stored.raw)
                self._append_current(session_id, "session/migrated", {"from": 0, "to": 1, "backup": backup})
        self._append_current(session_id, event_type, payload)

    def _write_atomic(
        self,
        session_id: str,
        events: List[Dict[str, object]],
        original: Optional[bytes],
    ) -> Optional[str]:
        path = self._path(session_id)
        backup_name: Optional[str] = None
        if original is not None:
            backups = self.root / "backups"
            backups.mkdir(exist_ok=True)
            digest = hashlib.sha256(original).hexdigest()
            backup_name = f"{session_id}.{digest}.jsonl"
            backup = backups / backup_name
            if backup.exists():
                if backup.read_bytes() != original:
                    raise SessionCorruptionError("备份哈希冲突")
            else:
                with backup.open("xb") as file:
                    file.write(original)
                    file.flush()
                    os.fsync(file.fileno())
        header = {
            "record": HEADER_RECORD,
            "format": SESSION_FORMAT_VERSION,
            "session_id": session_id,
            "created_at": events[0]["created_at"] if events else datetime.now(timezone.utc).isoformat(),
        }
        temporary = self.root / f".{session_id}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as file:
                file.write(json.dumps(header, ensure_ascii=False) + "\n")
                for event in events:
                    file.write(json.dumps(event, ensure_ascii=False) + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()
        return f"backups/{backup_name}" if backup_name is not None else None

    def repair(self, session_id: str) -> Dict[str, object]:
        with self.write_lease(session_id):
            if not self._path(session_id).exists():
                raise ValueError(f"unknown session {session_id}")
            stored = self._read(session_id, allow_torn=True)
            closers = open_turn_closers(stored.events)
            if stored.version == 1 and not stored.torn and not closers:
                return {"changed": False, "migrated": False, "torn": False, "tool_results": 0}
            backup = self._write_atomic(session_id, stored.events, stored.raw)
            if stored.version == 0:
                self._append_current(session_id, "session/migrated", {"from": 0, "to": 1, "backup": backup})
            if stored.torn or closers:
                last_time = stored.events[-1]["created_at"] if stored.events else datetime.now(timezone.utc).isoformat()
                for closer in closers:
                    self._append_current(session_id, str(closer["type"]), closer["payload"], last_time)
                self._append_current(
                    session_id,
                    "session/repaired",
                    {
                        "torn": stored.torn,
                        "tool_results": sum(1 for closer in closers if closer["type"] == "tool/result"),
                        "backup": backup,
                    },
                )
            return {
                "changed": True,
                "migrated": stored.version == 0,
                "torn": stored.torn,
                "tool_results": sum(1 for closer in closers if closer["type"] == "tool/result"),
                "backup": backup,
            }

    def migrate(self, session_id: str) -> Dict[str, object]:
        with self.write_lease(session_id):
            stored = self._read(session_id)
            if stored.version == 1:
                return {"changed": False, "from": 1, "to": 1}
            backup = self._write_atomic(session_id, stored.events, stored.raw)
            self._append_current(session_id, "session/migrated", {"from": 0, "to": 1, "backup": backup})
            return {"changed": True, "from": 0, "to": 1, "backup": backup}

    def migrate_all(self) -> List[Dict[str, object]]:
        results = []
        for path in self.root.glob("*.jsonl"):
            result = self.migrate(path.stem)
            results.append({"session_id": path.stem, **result})
        return results

    def fork(self, session_id: str, at_seq: Optional[int] = None) -> str:
        with self.write_lease(session_id):
            events = self.load(session_id)
            if not events:
                raise ValueError(f"unknown session {session_id}")
            if at_seq is not None:
                if not isinstance(at_seq, int) or isinstance(at_seq, bool) or at_seq < 0:
                    raise ValueError("at_seq 必须是非负整数")
                events = [event for event in events if event["seq"] <= at_seq]
                if not events:
                    raise ValueError("fork 位置没有可保留事件")
            closers = open_turn_closers(events, "forked")
            fork_id = uuid.uuid4().hex
            copied_events = []
            for event in events:
                copied = dict(event)
                copied["session_id"] = fork_id
                copied_events.append(copied)
            inherited_time = str(events[-1]["created_at"])
            for closer in closers:
                copied_events.append(
                    {
                        "session_id": fork_id,
                        "seq": len(copied_events),
                        "type": closer["type"],
                        "created_at": inherited_time,
                        "payload": closer["payload"],
                    }
                )
            if self._path(fork_id).exists():
                raise ValueError("fork 会话 ID 冲突")
            self._write_atomic(fork_id, copied_events, None)
            self.append(fork_id, "session/fork", {"source_session_id": session_id, "at_seq": at_seq})
            return fork_id

    def derive_system(self, session_id: str) -> str:
        system = ""
        for event in self.load(session_id):
            if event["type"] != "system/message":
                continue
            message = event["payload"].get("message", {})
            content = message.get("content", [])
            system = "".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
        return system

    def derive_messages(self, session_id: str) -> List[Dict[str, object]]:
        events = self.load(session_id)
        messages: List[Dict[str, object]] = []
        start = 0
        compaction_index: Optional[int] = None
        for index, event in enumerate(events):
            if event["type"] == "compaction/end":
                compaction_index = index
        if compaction_index is not None:
            start = compaction_index
            message = events[start]["payload"].get("message")
            if isinstance(message, dict):
                messages.append(message)
        for event in events[start + 1 :]:
            event_type = event["type"]
            payload = event["payload"]
            if event_type == "user/message" and isinstance(payload, dict):
                messages.append(payload)
            elif event_type == "assistant/message" and isinstance(payload.get("message"), dict):
                messages.append(payload["message"])
            elif event_type == "tool/result" and isinstance(payload, dict):
                messages.append(payload)
        return messages

    def latest_turn(self, session_id: str) -> int:
        latest = 0
        for event in self.load(session_id):
            if event["type"] in {"turn/start", "turn/end"}:
                turn = event["payload"].get("turn", 0)
                if isinstance(turn, int):
                    latest = max(latest, turn)
        return latest

    def list_sessions(self) -> List[Dict[str, object]]:
        sessions: List[Dict[str, object]] = []
        for path in self.root.glob("*.jsonl"):
            session_id = path.stem
            row: Dict[str, object] = {
                "session_id": session_id,
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            }
            try:
                stored = self._read(session_id)
                row.update({"events": len(stored.events), "format": stored.version, "state": "ok"})
            except (SessionCorruptionError, SessionFormatUnsupportedError, ValueError) as exc:
                row.update({"events": 0, "state": "unreadable", "error": str(exc)})
            sessions.append(row)
        return sorted(sessions, key=lambda item: str(item["updated_at"]), reverse=True)

    def transcript(self, session_id: str) -> str:
        lines: List[str] = []
        for message in self.derive_messages(session_id):
            role = str(message.get("role", "unknown"))
            content = message.get("content", [])
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "image":
                        attachment = block.get("attachment", {})
                        if isinstance(attachment, dict):
                            parts.append(f"[image: {attachment.get('path', 'unknown')}]\n")
                    else:
                        parts.append(str(block.get("text", "")))
                text = "".join(parts)
            else:
                text = ""
            if text:
                lines.append(f"[{role}]\n{text}")
        return "\n\n".join(lines)


class JsonlSessionPlugin(Plugin):
    name = "session.jsonl"
    provides = ("session",)

    def __init__(self, root: Path) -> None:
        self.root = root

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("session", JsonlSessionStore(self.root), self.name)
