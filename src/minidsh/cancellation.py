import threading
from typing import Callable, List, Optional


class RunCancelled(RuntimeError):
    code = "ABORTED"


class CancellationToken:
    def __init__(self, run_id: Optional[str] = None) -> None:
        self.run_id = run_id
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: List[Callable[[], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> bool:
        with self._lock:
            if self.cancelled:
                return False
            callbacks = list(self._callbacks)
            self._event.set()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                continue
        return True

    def throw_if_cancelled(self) -> None:
        if self.cancelled:
            raise RunCancelled("运行已取消")

    def wait(self, seconds: float) -> bool:
        return self._event.wait(seconds)

    def on_cancel(self, callback: Callable[[], None]) -> Callable[[], None]:
        with self._lock:
            if not self.cancelled:
                self._callbacks.append(callback)
                registered = True
            else:
                registered = False
        if not registered:
            callback()

        def unregister() -> None:
            with self._lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)

        return unregister
