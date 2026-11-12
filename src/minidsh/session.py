import json
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .plugins import Plugin, ServiceContext


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


class JsonlSessionStore(SessionStore):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    def append(self, session_id: str, event_type: str, payload: Dict[str, object]) -> None:
        with self._lock:
            path = self._path(session_id)
            sequence = sum(1 for _ in path.open("r", encoding="utf-8")) if path.exists() else 0
            event = {
                "session_id": session_id,
                "seq": sequence,
                "type": event_type,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            }
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def load(self, session_id: str) -> List[Dict[str, object]]:
        with self._lock:
            path = self._path(session_id)
            if not path.exists():
                return []
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def fork(self, session_id: str, at_seq: Optional[int] = None) -> str:
        events = self.load(session_id)
        if not events:
            raise ValueError(f"unknown session {session_id}")
        if at_seq is not None:
            if not isinstance(at_seq, int) or at_seq < 0:
                raise ValueError("at_seq 必须是非负整数")
            events = [event for event in events if event["seq"] <= at_seq]
            if not events:
                raise ValueError("fork 位置没有可保留事件")
        fork_id = uuid.uuid4().hex
        path = self._path(fork_id)
        if path.exists():
            raise ValueError("fork 会话 ID 冲突")
        with path.open("w", encoding="utf-8") as file:
            for event in events:
                copied = dict(event)
                copied["session_id"] = fork_id
                file.write(json.dumps(copied, ensure_ascii=False) + "\n")
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
            sessions.append(
                {
                    "session_id": path.stem,
                    "events": len(path.read_text(encoding="utf-8").splitlines()),
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                }
            )
        return sorted(sessions, key=lambda item: str(item["updated_at"]), reverse=True)

    def transcript(self, session_id: str) -> str:
        lines: List[str] = []
        for message in self.derive_messages(session_id):
            role = str(message.get("role", "unknown"))
            content = message.get("content", [])
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
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
