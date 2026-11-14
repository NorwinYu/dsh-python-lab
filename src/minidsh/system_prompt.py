import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .plugins import Plugin, ServiceContext


HARNESS_IDENTITY = "You are an AI agent powered by DeepSeek Harness."
READ_GUIDANCE = (
    "Use the read tool — not shell commands like cat, sed, head, or tail — to inspect text files. "
    "Use offset and limit to read a line range or continue a large file."
)
GLOB_GUIDANCE = "Use the glob tool — not shell find — to discover files by path pattern."
WRITE_GUIDANCE = "Read an existing file before overwriting it with write"
EDIT_GUIDANCE = "Read a file before editing it unless you just created or edited it in this session."
BASH_GUIDANCE = "Check the [exit code: N] marker on every bash result; investigate failures before moving on."
JOBS_GUIDANCE = (
    "Track every background job id you start. You are not notified in-session when a job finishes — do not busy-poll or sleep on one; "
    "keep working on independent steps and do not duplicate a running job's work. Before giving a final answer, collect every "
    "still-relevant job with job_output (set wait: true only when you are genuinely blocked on it), and job_kill jobs that stopped mattering."
)


@dataclass
class PromptSection:
    name: str
    order: int
    text: str


class SystemPrompt:
    def __init__(self) -> None:
        self._sections: Dict[str, PromptSection] = {
            "harness:identity": PromptSection("harness:identity", -1000, HARNESS_IDENTITY)
        }

    def register(self, name: str, order: int, text: str) -> None:
        if name in self._sections:
            raise ValueError(f"提示词段落已注册: {name}")
        self._sections[name] = PromptSection(name, order, text)

    def unregister(self, name: str) -> None:
        if name != "harness:identity":
            self._sections.pop(name, None)

    def snapshot(self) -> List[PromptSection]:
        return sorted(self._sections.values(), key=lambda section: (section.order, section.name))

    def render(self) -> str:
        sections: List[PromptSection] = sorted(
            self._sections.values(),
            key=lambda section: (section.order, section.name),
        )
        return "\n\n".join(section.text for section in sections if section.text)

    def render_without_tools(self) -> str:
        sections = sorted(self._sections.values(), key=lambda section: (section.order, section.name))
        return "\n\n".join(section.text for section in sections if section.text and not section.name.startswith("tool:"))

    def runtime_context(self, workspace: Path, sandbox_context: str = "", approval_context: str = "") -> str:
        sections = [
            f"Current working directory: {json.dumps(str(workspace), ensure_ascii=False)}.",
            sandbox_context,
            approval_context,
        ]
        body = "\n\n".join(section for section in sections if section)
        return f"Current runtime context. This snapshot supersedes earlier runtime-context snapshots.\n\n{body}"


class SystemPromptPlugin(Plugin):
    name = "core.system-prompt"
    provides = ("systemPrompt",)

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("systemPrompt", SystemPrompt(), self.name)
