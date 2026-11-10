import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional


DEFAULT_FEATURES: Dict[str, bool] = {
    "core.system-prompt": True,
    "core.tools": True,
    "tools.glob": True,
    "tools.read": True,
    "session.jsonl": True,
    "llm.mock": True,
    "llm.deepseek": False,
    "agent.core": True,
}


class ConfigError(ValueError):
    pass


@dataclass
class AppConfig:
    workspace: Path
    session_dir: Path
    features: Dict[str, bool]

    @classmethod
    def load(
        cls,
        workspace: Path,
        config_path: Optional[Path] = None,
        enable: Iterable[str] = (),
        disable: Iterable[str] = (),
    ) -> "AppConfig":
        workspace = workspace.expanduser().resolve()
        features = dict(DEFAULT_FEATURES)
        session_dir = Path(".minidsh/sessions")

        if config_path is not None:
            config_path = config_path.expanduser().resolve()
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigError(f"无法读取配置 {config_path}: {exc}") from exc
            if not isinstance(data, dict):
                raise ConfigError("配置文件顶层必须是 JSON 对象")
            unknown_keys = set(data) - {"session_dir", "features"}
            if unknown_keys:
                raise ConfigError(f"未知配置项: {', '.join(sorted(unknown_keys))}")
            configured_features = data.get("features", {})
            if not isinstance(configured_features, dict):
                raise ConfigError("features 必须是 JSON 对象")
            for name, value in configured_features.items():
                if name not in DEFAULT_FEATURES or not isinstance(value, bool):
                    raise ConfigError(f"无效功能开关: {name}={value!r}")
                features[name] = value
            session_dir = Path(data.get("session_dir", session_dir))

        cls._apply_overrides(features, enable, True)
        cls._apply_overrides(features, disable, False)

        if not session_dir.is_absolute():
            session_dir = workspace / session_dir
        return cls(workspace=workspace, session_dir=session_dir, features=features)

    @staticmethod
    def _apply_overrides(features: Dict[str, bool], names: Iterable[str], value: bool) -> None:
        for name in names:
            if name not in DEFAULT_FEATURES:
                raise ConfigError(f"未知功能: {name}")
            features[name] = value

    def public_dict(self) -> Dict[str, object]:
        return {
            "workspace": str(self.workspace),
            "session_dir": str(self.session_dir),
            "features": self.features,
        }
