from collections import defaultdict
from typing import Callable, DefaultDict, Dict, List


Listener = Callable[[Dict[str, object]], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: DefaultDict[str, List[Listener]] = defaultdict(list)

    def on(self, event: str, listener: Listener) -> Callable[[], None]:
        self._listeners[event].append(listener)

        def off() -> None:
            self._listeners[event].remove(listener)

        return off

    def emit(self, event: str, payload: Dict[str, object]) -> None:
        for listener in list(self._listeners[event]):
            listener(payload)
