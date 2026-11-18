import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from .discovery import PluginSpec, discover_plugins
from .sandbox import SANDBOX_MODES


DEFAULT_FEATURES: Dict[str, bool] = {
    "core.system-prompt": True,
    "core.tools": True,
    "core.sandbox-policy": True,
    "core.approval": True,
    "core.fs-observation": True,
    "core.jobs": True,
    "core.compaction": True,
    "core.cancellation": True,
    "core.streaming": True,
    "core.stream-recording": True,
    "core.sse-replay": True,
    "core.pty": True,
    "core.kernel-sandbox": False,
    "core.tool-result-pruner": False,
    "core.user-questions": True,
    "core.web": True,
    "core.subagents": False,
    "core.workflow": False,
    "core.mcp": False,
    "core.skills": False,
    "core.schedule": False,
    "core.goals": False,
    "core.goal-rounds": False,
    "core.ptc": False,
    "core.agent-presets": False,
    "tools.mcp-resources": False,
    "tools.run-code": False,
    "tools.skill": False,
    "tools.schedule": False,
    "tools.goal": False,
    "tools.workflow": False,
    "tools.subagent": False,
    "tools.subagent-fork": False,
    "tools.subagent-control": False,
    "tools.glob": True,
    "tools.read": True,
    "tools.write": True,
    "tools.edit": True,
    "tools.bash": True,
    "tools.bash-persistent": False,
    "tools.jobs": True,
    "tools.ask-user": False,
    "tools.ask-user-timed": False,
    "tools.todo": False,
    "tools.web-fetch": False,
    "tools.web-search": False,
    "session.jsonl": True,
    "llm.mock": True,
    "llm.deepseek": False,
    "llm.retry": True,
    "llm.model-listing": True,
    "llm.files": True,
    "agent.core": True,
}


class ConfigError(ValueError):
    pass


@dataclass
class AppConfig:
    workspace: Path
    session_dir: Path
    features: Dict[str, bool]
    plugin_specs: Tuple[PluginSpec, ...] = ()
    sandbox_mode: str = "read-only"
    approval_policy: Optional[str] = None
    approve_all: bool = False
    context_window: int = 1_048_576
    mcp_servers: Dict[str, Dict[str, object]] = field(default_factory=dict)
    skill_dirs: Tuple[Path, ...] = ()
    skill_include_default_roots: bool = True
    presets: Dict[str, Dict[str, object]] = field(default_factory=dict)
    default_preset: str = "standard"

    @classmethod
    def load(
        cls,
        workspace: Path,
        config_path: Optional[Path] = None,
        enable: Iterable[str] = (),
        disable: Iterable[str] = (),
        sandbox_mode: Optional[str] = None,
        approval_policy: Optional[str] = None,
        approve_all: bool = False,
        context_window: Optional[int] = None,
    ) -> "AppConfig":
        workspace = workspace.expanduser().resolve()
        plugin_specs = tuple(discover_plugins())
        external_names = {spec.name for spec in plugin_specs}
        conflicts = external_names.intersection(DEFAULT_FEATURES)
        if conflicts:
            raise ConfigError(f"external plugins must not override built-in features: {', '.join(sorted(conflicts))}")
        features = dict(DEFAULT_FEATURES)
        features.update({name: False for name in external_names})
        session_dir = Path(".minidsh/sessions")
        resolved_sandbox = "read-only"
        resolved_approval: Optional[str] = None
        resolved_approve_all = False
        resolved_context_window = 1_048_576
        resolved_mcp: Dict[str, Dict[str, object]] = {}
        resolved_skill_dirs: Tuple[Path, ...] = ()
        resolved_skill_defaults = True
        resolved_presets: Dict[str, Dict[str, object]] = {}
        resolved_default_preset = "standard"

        if config_path is not None:
            config_path = config_path.expanduser().resolve()
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigError(f"cannot read config {config_path}: {exc}") from exc
            if not isinstance(data, dict):
                raise ConfigError("config file top level must be a JSON object")
            allowed = {"session_dir", "features", "sandbox_mode", "approval_policy", "approve_all", "context_window", "mcp_servers", "skill_dirs", "skill_include_default_roots", "presets", "default_preset"}
            unknown_keys = set(data) - allowed
            if unknown_keys:
                raise ConfigError(f"unknown config keys: {', '.join(sorted(unknown_keys))}")
            configured_features = data.get("features", {})
            if not isinstance(configured_features, dict):
                raise ConfigError("features must be a JSON object")
            for name, value in configured_features.items():
                if name not in features or not isinstance(value, bool):
                    raise ConfigError(f"invalid feature flag: {name}={value!r}")
                features[name] = value
            session_dir = Path(data.get("session_dir", session_dir))
            resolved_sandbox = data.get("sandbox_mode", resolved_sandbox)
            resolved_approval = data.get("approval_policy", resolved_approval)
            resolved_approve_all = data.get("approve_all", resolved_approve_all)
            resolved_context_window = data.get("context_window", resolved_context_window)
            configured_mcp = data.get("mcp_servers", {})
            if not isinstance(configured_mcp, dict) or any(not isinstance(value, dict) for value in configured_mcp.values()):
                raise ConfigError("mcp_servers must be a mapping of server configuration objects")
            resolved_mcp = configured_mcp
            configured_skill_dirs = data.get("skill_dirs", [])
            if not isinstance(configured_skill_dirs, list) or any(not isinstance(value, str) or not value for value in configured_skill_dirs):
                raise ConfigError("skill_dirs must be an array of non-empty path strings")
            resolved_skill_dirs = tuple((workspace / value).expanduser().resolve() if not Path(value).is_absolute() else Path(value).expanduser().resolve() for value in configured_skill_dirs)
            resolved_skill_defaults = data.get("skill_include_default_roots", True)
            if not isinstance(resolved_skill_defaults, bool):
                raise ConfigError("skill_include_default_roots must be a boolean")
            configured_presets = data.get("presets", {})
            if not isinstance(configured_presets, dict) or any(not isinstance(value, dict) for value in configured_presets.values()):
                raise ConfigError("presets must be a mapping of configuration objects")
            resolved_presets = configured_presets
            resolved_default_preset = data.get("default_preset", "standard")
            if not isinstance(resolved_default_preset, str) or not resolved_default_preset:
                raise ConfigError("default_preset must be a non-empty string")

        cls._apply_overrides(features, enable, True)
        cls._apply_overrides(features, disable, False)
        if features["tools.bash-persistent"]:
            features["tools.bash"] = False
        resolved_sandbox = sandbox_mode or resolved_sandbox
        resolved_approval = approval_policy or resolved_approval
        resolved_approve_all = approve_all or resolved_approve_all
        resolved_context_window = context_window if context_window is not None else resolved_context_window

        if resolved_sandbox not in SANDBOX_MODES:
            raise ConfigError(f"unknown sandbox policy: {resolved_sandbox}")
        if resolved_approval not in {None, "ask", "never"}:
            raise ConfigError(f"unknown approval policy: {resolved_approval}")
        if not isinstance(resolved_approve_all, bool):
            raise ConfigError("approve_all must be a boolean")
        if not isinstance(resolved_context_window, int) or resolved_context_window <= 0:
            raise ConfigError("context_window must be a positive integer")
        if not session_dir.is_absolute():
            session_dir = workspace / session_dir
        return cls(
            workspace=workspace,
            session_dir=session_dir,
            features=features,
            plugin_specs=plugin_specs,
            sandbox_mode=resolved_sandbox,
            approval_policy=resolved_approval,
            approve_all=resolved_approve_all,
            context_window=resolved_context_window,
            mcp_servers=resolved_mcp,
            skill_dirs=resolved_skill_dirs,
            skill_include_default_roots=resolved_skill_defaults,
            presets=resolved_presets,
            default_preset=resolved_default_preset,
        )

    @staticmethod
    def _apply_overrides(features: Dict[str, bool], names: Iterable[str], value: bool) -> None:
        for name in names:
            if name not in features:
                raise ConfigError(f"unknown feature: {name}")
            features[name] = value

    def resolved_approval_policy(self) -> str:
        if self.approval_policy is not None:
            return self.approval_policy
        return "never" if self.sandbox_mode == "danger-full-access" else "ask"

    def public_dict(self) -> Dict[str, object]:
        return {
            "workspace": str(self.workspace),
            "session_dir": str(self.session_dir),
            "sandbox_mode": self.sandbox_mode,
            "approval_policy": self.resolved_approval_policy(),
            "approve_all": self.approve_all,
            "context_window": self.context_window,
            "mcp_servers": self.mcp_servers,
            "skill_dirs": [str(path) for path in self.skill_dirs],
            "skill_include_default_roots": self.skill_include_default_roots,
            "presets": self.presets,
            "default_preset": self.default_preset,
            "features": self.features,
        }
