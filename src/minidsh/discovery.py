import re
from dataclasses import dataclass
from importlib.metadata import EntryPoint, distributions
from typing import TYPE_CHECKING, Dict, List, Set, Tuple

if TYPE_CHECKING:
    from .plugins import Plugin


ENTRY_POINT_GROUP = "minidsh.plugins"
PLUGIN_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")


class DiscoveryError(ValueError):
    pass


@dataclass(frozen=True)
class PluginSpec:
    entry_point: EntryPoint
    distribution_name: str
    distribution_version: str

    @property
    def name(self) -> str:
        return self.entry_point.name

    @property
    def distribution(self) -> str:
        return self.distribution_name

    @property
    def version(self) -> str:
        return self.distribution_version

    @property
    def value(self) -> str:
        return self.entry_point.value

    def load(self) -> "Plugin":
        from .plugins import Plugin

        loaded = self.entry_point.load()
        if isinstance(loaded, Plugin):
            plugin = loaded
        else:
            if not callable(loaded):
                raise DiscoveryError(f"plugin {self.name} must export a Plugin, a Plugin subclass, or a factory")
            plugin = loaded()
        if not isinstance(plugin, Plugin):
            raise DiscoveryError(f"factory for plugin {self.name} did not return a Plugin")
        if plugin.name != self.name:
            raise DiscoveryError(f"plugin name mismatch: entry point={self.name}, Plugin={plugin.name}")
        return plugin


def discover_plugins() -> List[PluginSpec]:
    specs: List[PluginSpec] = []
    seen: Set[Tuple[str, str, str, str]] = set()
    owners: Dict[str, Tuple[str, str]] = {}
    for distribution in distributions():
        distribution_name = distribution.metadata.get("Name", "unknown")
        for entry_point in distribution.entry_points:
            if entry_point.group != ENTRY_POINT_GROUP:
                continue
            if not PLUGIN_NAME.fullmatch(entry_point.name):
                raise DiscoveryError(f"invalid external plugin name: {entry_point.name}")
            key = (entry_point.name, entry_point.value, distribution_name, distribution.version)
            if key in seen:
                continue
            seen.add(key)
            owner = (distribution_name, entry_point.value)
            if entry_point.name in owners:
                raise DiscoveryError(f"external plugin entry point name conflict: {entry_point.name}")
            owners[entry_point.name] = owner
            specs.append(PluginSpec(entry_point, distribution_name, distribution.version))
    return sorted(specs, key=lambda spec: spec.name)
