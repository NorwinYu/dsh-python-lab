import json
from pathlib import Path

from .plugins import Plugin, ServiceContext


SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}


class SandboxPolicy:
    def __init__(self, mode: str, workspace: Path) -> None:
        if mode not in SANDBOX_MODES:
            raise ValueError(f"未知文件策略: {mode}")
        self.mode = mode
        self.workspace = workspace

    def context(self) -> str:
        if self.mode == "read-only":
            return (
                "Current DSH file policy: read-only. Any available operation enforced by the DSH file sandbox cannot modify files "
                "in the standing mode. Do not refuse a required modification from this policy alone: try an available tool normally "
                "and follow any denial and escalation guidance it returns."
            )
        if self.mode == "workspace-write":
            root = json.dumps(str(self.workspace), ensure_ascii=False)
            return (
                "Current DSH file policy: workspace-write. Any available operation enforced by the DSH file sandbox may modify files "
                f"under the session workspace: {root}. Some platform temporary areas may also be writable."
            )
        return (
            "Current DSH file policy: danger-full-access. The DSH file sandbox does not restrict file modifications by available operations."
        )


class SandboxPolicyPlugin(Plugin):
    name = "core.sandbox-policy"
    provides = ("sandboxPolicy",)

    def __init__(self, mode: str, workspace: Path) -> None:
        self.mode = mode
        self.workspace = workspace

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("sandboxPolicy", SandboxPolicy(self.mode, self.workspace), self.name)
