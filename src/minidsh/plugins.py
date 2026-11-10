from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Sequence, Tuple

from .events import EventBus


class ServiceContext:
    def __init__(self, events: EventBus) -> None:
        self.events = events
        self._services: Dict[str, object] = {}
        self._owners: Dict[str, str] = {}

    def provide(self, name: str, service: object, owner: str) -> None:
        if name in self._services:
            raise ValueError(f"服务已存在: {name}")
        self._services[name] = service
        self._owners[name] = owner

    def remove_by_owner(self, owner: str) -> None:
        for name, service_owner in list(self._owners.items()):
            if service_owner == owner:
                del self._owners[name]
                del self._services[name]

    def require(self, name: str) -> object:
        if name not in self._services:
            raise KeyError(f"缺少服务: {name}")
        return self._services[name]

    def optional(self, name: str) -> Optional[object]:
        return self._services.get(name)

    def missing(self, names: Sequence[str]) -> List[str]:
        return [name for name in names if name not in self._services]


class Plugin(ABC):
    name: str
    provides: Tuple[str, ...] = ()
    requires: Tuple[str, ...] = ()

    @abstractmethod
    def activate(self, ctx: ServiceContext) -> None:
        pass

    def deactivate(self, ctx: ServiceContext) -> None:
        pass


class PluginManager:
    def __init__(self, ctx: ServiceContext, features: Dict[str, bool]) -> None:
        self.ctx = ctx
        self.features = features
        self._plugins: Dict[str, Plugin] = {}
        self._states: Dict[str, str] = {}
        self._errors: Dict[str, str] = {}

    def register(self, plugin: Plugin) -> None:
        if plugin.name in self._plugins:
            raise ValueError(f"插件已注册: {plugin.name}")
        self._plugins[plugin.name] = plugin
        self._states[plugin.name] = "pending" if self.features[plugin.name] else "disabled"

    def activate_all(self) -> None:
        while True:
            progressed = False
            for name, plugin in self._plugins.items():
                if self._states[name] != "pending":
                    continue
                if self.ctx.missing(plugin.requires):
                    continue
                try:
                    plugin.activate(self.ctx)
                except Exception as exc:
                    self._states[name] = "error"
                    self._errors[name] = str(exc)
                else:
                    self._states[name] = "active"
                    progressed = True
            if not progressed:
                return

    def deactivate_all(self) -> None:
        for name, plugin in reversed(list(self._plugins.items())):
            if self._states[name] != "active":
                continue
            plugin.deactivate(self.ctx)
            self.ctx.remove_by_owner(name)
            self._states[name] = "pending" if self.features[name] else "disabled"

    def status(self) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for name, plugin in self._plugins.items():
            row: Dict[str, object] = {
                "name": name,
                "state": self._states[name],
                "provides": list(plugin.provides),
                "requires": list(plugin.requires),
                "missing": self.ctx.missing(plugin.requires),
            }
            if name in self._errors:
                row["error"] = self._errors[name]
            rows.append(row)
        return rows
