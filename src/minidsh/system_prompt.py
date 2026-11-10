import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from .plugins import Plugin, ServiceContext


HARNESS_IDENTITY = "You are an AI agent powered by DeepSeek Harness."
READ_GUIDANCE = (
    "Use the read tool — not shell commands like cat, sed, head, or tail — to inspect text files. "
    "Use offset and limit to read a line range or continue a large file."
)
GLOB_GUIDANCE = "Use the glob tool — not shell find — to discover files by path pattern."


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

    def render(self) -> str:
        sections: List[PromptSection] = sorted(
            self._sections.values(),
            key=lambda section: (section.order, section.name),
        )
        return "\n\n".join(section.text for section in sections if section.text)

    def runtime_context(self, workspace: Path) -> str:
        current = f"Current working directory: {json.dumps(str(workspace), ensure_ascii=False)}."
        return (
            "Current runtime context. This snapshot supersedes earlier runtime-context snapshots.\n\n"
            f"{current}"
        )


class SystemPromptPlugin(Plugin):
    name = "core.system-prompt"
    provides = ("systemPrompt",)

    def activate(self, ctx: ServiceContext) -> None:
        ctx.provide("systemPrompt", SystemPrompt(), self.name)
