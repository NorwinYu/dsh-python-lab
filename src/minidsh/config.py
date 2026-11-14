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
    "tools.mcp-resources": False,
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
            raise ConfigError(f"外部插件不能覆盖内置功能: {', '.join(sorted(conflicts))}")
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

        if config_path is not None:
            config_path = config_path.expanduser().resolve()
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigError(f"无法读取配置 {config_path}: {exc}") from exc
            if not isinstance(data, dict):
                raise ConfigError("配置文件顶层必须是 JSON 对象")
            allowed = {"session_dir", "features", "sandbox_mode", "approval_policy", "approve_all", "context_window", "mcp_servers", "skill_dirs", "skill_include_default_roots"}
            unknown_keys = set(data) - allowed
            if unknown_keys:
                raise ConfigError(f"未知配置项: {', '.join(sorted(unknown_keys))}")
            configured_features = data.get("features", {})
            if not isinstance(configured_features, dict):
                raise ConfigError("features 必须是 JSON 对象")
            for name, value in configured_features.items():
                if name not in features or not isinstance(value, bool):
                    raise ConfigError(f"无效功能开关: {name}={value!r}")
                features[name] = value
            session_dir = Path(data.get("session_dir", session_dir))
            resolved_sandbox = data.get("sandbox_mode", resolved_sandbox)
            resolved_approval = data.get("approval_policy", resolved_approval)
            resolved_approve_all = data.get("approve_all", resolved_approve_all)
            resolved_context_window = data.get("context_window", resolved_context_window)
            configured_mcp = data.get("mcp_servers", {})
            if not isinstance(configured_mcp, dict) or any(not isinstance(value, dict) for value in configured_mcp.values()):
                raise ConfigError("mcp_servers 必须是服务器配置对象映射")
            resolved_mcp = configured_mcp
            configured_skill_dirs = data.get("skill_dirs", [])
            if not isinstance(configured_skill_dirs, list) or any(not isinstance(value, str) or not value for value in configured_skill_dirs):
                raise ConfigError("skill_dirs 必须是非空路径字符串数组")
            resolved_skill_dirs = tuple((workspace / value).expanduser().resolve() if not Path(value).is_absolute() else Path(value).expanduser().resolve() for value in configured_skill_dirs)
            resolved_skill_defaults = data.get("skill_include_default_roots", True)
            if not isinstance(resolved_skill_defaults, bool):
                raise ConfigError("skill_include_default_roots 必须是布尔值")

        cls._apply_overrides(features, enable, True)
        cls._apply_overrides(features, disable, False)
        if features["tools.bash-persistent"]:
            features["tools.bash"] = False
        resolved_sandbox = sandbox_mode or resolved_sandbox
        resolved_approval = approval_policy or resolved_approval
        resolved_approve_all = approve_all or resolved_approve_all
        resolved_context_window = context_window if context_window is not None else resolved_context_window

        if resolved_sandbox not in SANDBOX_MODES:
            raise ConfigError(f"未知文件策略: {resolved_sandbox}")
        if resolved_approval not in {None, "ask", "never"}:
            raise ConfigError(f"未知审批策略: {resolved_approval}")
        if not isinstance(resolved_approve_all, bool):
            raise ConfigError("approve_all 必须是布尔值")
        if not isinstance(resolved_context_window, int) or resolved_context_window <= 0:
            raise ConfigError("context_window 必须是正整数")
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
        )

    @staticmethod
    def _apply_overrides(features: Dict[str, bool], names: Iterable[str], value: bool) -> None:
        for name in names:
            if name not in features:
                raise ConfigError(f"未知功能: {name}")
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
            "features": self.features,
        }
