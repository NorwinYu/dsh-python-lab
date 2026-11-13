from pathlib import Path
from typing import List, Optional

from .plugins import Plugin, ServiceContext


class SeatbeltUnavailableError(RuntimeError):
    pass


def _subpath(path: Path) -> str:
    value = str(path.resolve())
    if "\0" in value:
        raise ValueError("路径不能包含 NUL")
    return value


def profile(workspace: Path, mode: str, job_root: Optional[Path] = None) -> str:
    if mode == "danger-full-access":
        return "(version 1)\n(allow default)\n"
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow file-read*)",
        "(allow process-exec process-fork process-info* signal mach-lookup sysctl-read pseudo-tty)",
        "(allow network-outbound)",
    ]
    if mode == "workspace-write":
        paths = [_subpath(workspace)]
        if job_root is not None:
            paths.append(_subpath(job_root))
        quoted_paths = " ".join(f'(subpath "{path}")' for path in paths)
        rules.append(f"(allow file-write* (subpath \"/private/tmp\") {quoted_paths})")
    return "\n".join(rules) + "\n"


class SeatbeltService:
    def __init__(self, workspace: Path, job_root: Path) -> None:
        self.workspace = workspace
        self.job_root = job_root

    def argv(self, mode: str, command: Optional[str] = None, interactive: bool = False) -> List[str]:
        if mode == "danger-full-access":
            return ["/bin/bash", "--noprofile", "--norc", "-i"] if interactive else ["/bin/bash", "-c", command or ""]
        sandbox_profile = profile(self.workspace, mode, self.job_root)
        shell = ["/bin/bash", "--noprofile", "--norc", "-i"] if interactive else ["/bin/bash", "-c", command or ""]
        return ["/usr/bin/sandbox-exec", "-p", sandbox_profile, *shell]


class SeatbeltPlugin(Plugin):
    name = "core.kernel-sandbox"
    provides = ("kernelSandbox",)

    def __init__(self, workspace: Path, job_root: Path) -> None:
        self.workspace = workspace
        self.job_root = job_root

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("kernelSandbox", SeatbeltService(self.workspace, self.job_root), self.name)
