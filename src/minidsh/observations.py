import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from .plugins import Plugin, ServiceContext
from .session import SessionStore


class ObservationStore:
    def __init__(self) -> None:
        self._versions: Dict[Tuple[str, str], Tuple[int, int]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def version(path: Path) -> Tuple[int, int]:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size

    def record(self, session_id: Optional[str], path: Path, session: Optional[SessionStore] = None) -> None:
        if session_id is None:
            return
        version = self.version(path)
        with self._lock:
            self._versions[(session_id, str(path))] = version
        if session is not None:
            session.append(session_id, "fs/observed", {"path": str(path), "version": list(version)})

    def restore(self, session_id: str, path: Path, version: Tuple[int, int]) -> None:
        with self._lock:
            self._versions[(session_id, str(path))] = version

    def require_current(self, session_id: Optional[str], path: Path) -> None:
        with self._lock:
            if session_id is None or (session_id, str(path)) not in self._versions:
                raise ValueError(f"修改前必须先读取文件: {path}")
            if self._versions[(session_id, str(path))] != self.version(path):
                raise ValueError(f"文件在读取后已变化，请重新读取: {path}")


class ObservationPlugin(Plugin):
    name = "core.fs-observation"
    provides = ("observations",)

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("observations", ObservationStore(), self.name)
