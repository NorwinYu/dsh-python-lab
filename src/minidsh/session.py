import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .plugins import Plugin, ServiceContext


class SessionStore(ABC):
    @abstractmethod
    def append(self, session_id: str, event_type: str, payload: Dict[str, object]) -> None:
        pass

    @abstractmethod
    def load(self, session_id: str) -> List[Dict[str, object]]:
        pass


class JsonlSessionStore(SessionStore):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    def append(self, session_id: str, event_type: str, payload: Dict[str, object]) -> None:
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
        path = self._path(session_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class JsonlSessionPlugin(Plugin):
    name = "session.jsonl"
    provides = ("session",)

    def __init__(self, root: Path) -> None:
        self.root = root

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("session", JsonlSessionStore(self.root), self.name)
