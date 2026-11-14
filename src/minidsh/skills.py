import hashlib
import html
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .llm import text_block
from .plugins import Plugin, ServiceContext
from .session import SessionStore
from .tools import Execution, Tool, ToolError, ToolRegistry


SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_GESTURE = re.compile(r"(?:^|\s)/([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$)")
BOOLEAN_WORDS = {
    "true": True, "yes": True, "on": True, "1": True,
    "false": False, "no": False, "off": False, "0": False,
}


@dataclass
class Skill:
    name: str
    description: str
    content: str
    provider: str = "runtime"
    path: Optional[Path] = None
    resource_base: Optional[Dict[str, str]] = None
    model_invocable: bool = True
    user_invocable: bool = True
    when_to_use: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class SkillCandidate:
    skill: Skill
    rank: int
    order: int


class FilesystemSkills:
    def __init__(self, workspace: Path, custom_dirs: Tuple[Path, ...], include_defaults: bool = True) -> None:
        self.workspace = workspace
        self.custom_dirs = custom_dirs
        self.include_defaults = include_defaults

    def roots(self) -> List[Tuple[str, int, Path]]:
        roots: List[Tuple[str, int, Path]] = []
        if self.include_defaults:
            project = self.workspace
            for parent in (self.workspace, *self.workspace.parents):
                if (parent / ".git").exists():
                    project = parent
                    break
            roots.extend([
                ("project-dsh", 100, project / ".dsh" / "skills"),
                ("project-agents", 200, project / ".agents" / "skills"),
            ])
        roots.extend(("custom", 300, path) for path in self.custom_dirs)
        if self.include_defaults:
            roots.extend([
                ("user-dsh", 400, Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))) / "skills"),
                ("user-agents", 500, Path(os.environ.get("DSH_AGENTS_HOME", str(Path.home() / ".agents"))) / "skills"),
            ])
            bundled = os.environ.get("DSH_BUNDLED_SKILL_DIR")
            if bundled:
                roots.append(("bundled", 600, Path(bundled)))
        return roots

    def snapshot(self) -> Tuple[List[SkillCandidate], bool]:
        candidates: List[SkillCandidate] = []
        complete = True
        order = 0
        for provider, rank, root in self.roots():
            try:
                skills = self._scan_root(provider, root)
            except (OSError, UnicodeError):
                complete = False
                continue
            for skill in skills:
                candidates.append(SkillCandidate(skill, rank, order))
                order += 1
        return candidates, complete

    def _scan_root(self, provider: str, root: Path) -> List[Skill]:
        if not root.exists():
            return []
        if not root.is_dir():
            raise OSError(f"skill root is not a directory: {root}")
        skills: List[Skill] = []
        for entry in root.iterdir():
            if provider == "user-dsh" and entry.name == ".system":
                continue
            if entry.is_dir():
                path = entry / "SKILL.md"
                if not path.is_file():
                    continue
            elif entry.is_file() and entry.name.endswith(".md"):
                path = entry
            else:
                continue
            try:
                skill = parse_skill_file(path, provider)
            except (ValueError, UnicodeError):
                continue
            if skill is not None:
                skills.append(skill)
        return skills


def parse_boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in BOOLEAN_WORDS:
        raise ValueError(f"invalid skill boolean: {value}")
    return BOOLEAN_WORDS[normalized]


def scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith('"'):
        return str(json.loads(value))
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1].replace("''", "'")
    return value.split(" #", 1)[0].strip()


def parse_skill_file(path: Path, provider: str) -> Optional[Skill]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    line_end = text.find("\n", end + 1)
    if line_end < 0 or text[end + 1:line_end].strip() != "---":
        return None
    fields: Dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        fields[key.strip()] = scalar(value)
    name = fields.get("name", "")
    description = fields.get("description", "")
    if not SKILL_NAME.fullmatch(name) or not description.strip():
        return None
    model = not parse_boolean(fields["disable-model-invocation"]) if "disable-model-invocation" in fields else True
    user = parse_boolean(fields["user-invocable"]) if "user-invocable" in fields else True
    return Skill(
        name=name,
        description=description.strip(),
        content=text[line_end + 1:].strip(),
        provider=provider,
        path=path,
        resource_base={"kind": "directory", "path": str(path.parent)},
        model_invocable=model,
        user_invocable=user,
        when_to_use=fields.get("whenToUse") or None,
    )


class SkillRegistry:
    def __init__(self, filesystem: FilesystemSkills) -> None:
        self.filesystem = filesystem
        self._runtime: List[Skill] = []
        self._providers: List[Tuple[str, int, object]] = []

    def register(self, skill: Skill) -> None:
        self._validate(skill)
        if any(item.name == skill.name for item in self._runtime):
            return
        self._runtime.append(skill)

    def register_provider(self, name: str, provider: object, rank: int = 250) -> None:
        if name == "runtime" or any(item[0] == name for item in self._providers):
            raise ValueError(f"skill provider already registered: {name}")
        self._providers.append((name, rank, provider))

    def snapshot(self) -> Tuple[List[Skill], bool]:
        candidates = [SkillCandidate(skill, 0, index) for index, skill in enumerate(self._runtime)]
        filesystem_candidates, complete = self.filesystem.snapshot()
        candidates.extend(filesystem_candidates)
        for provider_name, rank, provider in self._providers:
            try:
                result = provider.snapshot()
                rows, provider_complete = result
                for index, skill in enumerate(rows):
                    skill.provider = provider_name
                    self._validate(skill)
                    candidates.append(SkillCandidate(skill, rank, index))
                complete = complete and provider_complete
            except Exception:
                complete = False
        candidates.sort(key=lambda item: (item.rank, item.order))
        winners: Dict[str, Skill] = {}
        for candidate in candidates:
            self._validate(candidate.skill)
            if candidate.skill.name not in winners:
                winners[candidate.skill.name] = candidate.skill
        return sorted(winners.values(), key=lambda skill: skill.name), complete

    def list(self) -> List[Skill]:
        return self.snapshot()[0]

    def get(self, name: str) -> Optional[Skill]:
        if not SKILL_NAME.fullmatch(name):
            return None
        skill = next((item for item in self.list() if item.name == name), None)
        if skill is None:
            return None
        if skill.path is not None:
            loaded = parse_skill_file(skill.path, skill.provider)
            if loaded is None or loaded.name != name:
                return None
            return loaded
        self._validate(skill)
        return skill

    @staticmethod
    def _validate(skill: Skill) -> None:
        if not SKILL_NAME.fullmatch(skill.name) or not skill.description.strip():
            raise ValueError("invalid skill name or description")
        if not isinstance(skill.model_invocable, bool) or not isinstance(skill.user_invocable, bool):
            raise ValueError("skill invocation policies must be boolean")


def render_skill(skill: Skill) -> str:
    base = skill.resource_base
    if base is None:
        guidance = f'Resources for this skill are managed by provider "{skill.provider}".\nLoad referenced resources only as needed.'
    elif base.get("kind") == "directory":
        guidance = f"Base directory for this skill: {base.get('path', '')}\nResolve relative paths mentioned by this skill against the base directory before using them. Load referenced resources only as needed."
    elif base.get("kind") == "url":
        guidance = f"Base URL for this skill: {base.get('url', '')}\nResolve relative URLs mentioned by this skill against the base URL before using them. Load referenced resources only as needed."
    else:
        guidance = f"Resources for this skill: {base.get('description', '')}\nLoad referenced resources only as needed."
    return (
        f'<skill_content name="{html.escape(skill.name, quote=True)}">\n'
        f"<skill_resources>\n{guidance}\n</skill_resources>\n\n"
        f"<skill_instructions>\n{skill.content}\n</skill_instructions>\n</skill_content>"
    )


def catalog_description(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[:limit - 3] + "..."


def catalog_digest(entries: List[Dict[str, str]], incomplete: bool) -> str:
    canonical = "\n".join(json.dumps([entry["name"], entry["description"]], ensure_ascii=False) for entry in entries)
    return hashlib.sha256(("incomplete\n" if incomplete else "").encode() + canonical.encode()).hexdigest()


def render_catalog(entries: List[Dict[str, str]], incomplete: bool, update: bool) -> str:
    lines = ["- `" + entry["name"] + "`: " + html.escape(entry["description"]) for entry in entries]
    notice = "Some skills could not be checked. Only confirmed skills are listed; a lower-priority definition of the same name may be selected when a preferred source is unavailable. Discovery will be retried on the next request."
    if not update:
        parts = [
            "<system-reminder>",
            "No skills could be confirmed in this discovery attempt." if not entries else "A skill is a reusable set of task-specific instructions. The following skills are available in this session:",
            "", "<available_skills>", *lines, "</available_skills>", "",
        ]
        if incomplete:
            parts.extend([notice, ""])
        parts.extend([
            "Wait for an updated catalog before calling the `skill` tool. Do not infer skill instructions from an empty catalog." if not entries else "If the user names a skill, or the task clearly matches a skill's description, call the `skill` tool with the exact skill name before taking task actions. Load all applicable skills, then follow their full instructions. This catalog contains summaries only; do not infer or follow a skill's instructions until it has been loaded.",
            "A user may also invoke a skill directly; its <skill_content> block then appears in this conversation. Follow it, and do not call the `skill` tool again for that skill.",
            "</system-reminder>",
        ])
        return "\n".join(parts)
    parts = [
        "<system-reminder>",
        "Skill discovery is incomplete. This catalog of currently available skills replaces every earlier available-skills list in this session:" if incomplete else "The available skill catalog changed. This complete catalog replaces every earlier available-skills list in this session:",
        "", "<available_skills>", *lines, "</available_skills>", "",
    ]
    if incomplete:
        parts.extend([notice, ""])
    if entries:
        parts.append("Use only names in this replacement catalog. If the user names a listed skill, or the task clearly matches its description, call the `skill` tool with the exact name before acting.")
        parts.append("A user may also invoke a skill directly; its <skill_content> block then appears in this conversation. Follow it, and do not call the `skill` tool again for that skill.")
    else:
        parts.append("No skills could be confirmed in this discovery attempt. Do not use names from earlier skill catalogs." if incomplete else "No skills are currently available through the `skill` tool. Do not use names from earlier skill catalogs.")
        parts.append("A user may still invoke a skill directly; its <skill_content> block then appears in this conversation. Follow it, and do not call the `skill` tool for it.")
    parts.append("</system-reminder>")
    return "\n".join(parts)


class SkillService:
    def __init__(self, registry: SkillRegistry, session: SessionStore, tool_enabled: bool, description_limit: int = 500) -> None:
        if description_limit < 3:
            raise ValueError("MINIDSH_SKILL_DESCRIPTION_MAX must be at least 3")
        self.registry = registry
        self.session = session
        self.tool_enabled = tool_enabled
        self.description_limit = description_limit

    def snapshot(self) -> Dict[str, object]:
        skills, complete = self.registry.snapshot()
        return {"skills": [self.public(skill) for skill in skills], "complete": complete}

    @staticmethod
    def public(skill: Skill) -> Dict[str, object]:
        return {
            "name": skill.name,
            "description": skill.description,
            "provider": skill.provider,
            "path": str(skill.path) if skill.path is not None else None,
            "modelInvocable": skill.model_invocable,
            "userInvocable": skill.user_invocable,
            "whenToUse": skill.when_to_use,
        }

    def load(self, name: object, model: bool = True) -> str:
        if not isinstance(name, str) or not SKILL_NAME.fullmatch(name):
            raise ToolError(f'invalid skill name "{name}"')
        skill = self.registry.get(name)
        if skill is None:
            raise ToolError(f'skill "{name}" is unknown or no longer available')
        if model and not skill.model_invocable:
            raise ToolError(f'skill "{name}" is not available for model invocation')
        if not model and not skill.user_invocable:
            raise ToolError(f'skill "{name}" is not available for user invocation')
        return render_skill(skill)

    def before_step(self, session_id: str, step: int, prompt: str) -> List[Dict[str, object]]:
        messages: List[Dict[str, object]] = []
        if step == 1:
            for name in dict.fromkeys(SKILL_GESTURE.findall(prompt)):
                skill = self.registry.get(name)
                if skill is None or not skill.user_invocable:
                    continue
                messages.append({
                    "role": "user",
                    "content": [text_block(render_skill(skill))],
                    "source": {"kind": "skill-invocation", "name": name, "form": "instructions"},
                })
        skills, complete = self.registry.snapshot() if self.tool_enabled else ([], True)
        entries = [
            {"name": skill.name, "description": catalog_description(skill.description, self.description_limit)}
            for skill in skills if skill.model_invocable
        ]
        incomplete = not complete
        digest = catalog_digest(entries, incomplete)
        published = False
        visible_digest: Optional[str] = None
        events = self.session.load(session_id)
        visible = set(self.session.surface(session_id)["nodes"])
        for event in reversed(events):
            if event["type"] != "user/message":
                continue
            source = event["payload"].get("source", {})
            if not isinstance(source, dict) or source.get("kind") != "skill-catalog" or not isinstance(source.get("entries"), list):
                continue
            previous = source["entries"]
            if any(not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("description"), str) for entry in previous):
                continue
            published = True
            if event["seq"] in visible:
                visible_digest = catalog_digest(previous, source.get("incomplete") is True)
                break
        if visible_digest == digest:
            return messages
        if not published and not entries and not incomplete:
            return messages
        source: Dict[str, object] = {"kind": "skill-catalog", "form": "catalog", "entries": entries}
        if published:
            source["update"] = True
        if incomplete:
            source["incomplete"] = True
        messages.append({
            "role": "user",
            "content": [text_block(render_catalog(entries, incomplete, published))],
            "source": source,
        })
        return messages


class SkillsPlugin(Plugin):
    name = "core.skills"
    provides = ("skills",)
    requires = ("session",)

    def __init__(self, workspace: Path, custom_dirs: Tuple[Path, ...], include_defaults: bool, tool_enabled: bool) -> None:
        self.workspace = workspace
        self.custom_dirs = custom_dirs
        self.include_defaults = include_defaults
        self.tool_enabled = tool_enabled

    def activate(self, ctx: ServiceContext) -> None:
        session = ctx.require("session")
        assert isinstance(session, SessionStore)
        registry = SkillRegistry(FilesystemSkills(self.workspace, self.custom_dirs, self.include_defaults))
        try:
            limit = int(os.environ.get("MINIDSH_SKILL_DESCRIPTION_MAX", "500"))
        except ValueError as exc:
            raise ValueError("MINIDSH_SKILL_DESCRIPTION_MAX must be an integer") from exc
        ctx.provide("skills", SkillService(registry, session, self.tool_enabled, limit), self.name)


class SkillToolPlugin(Plugin):
    name = "tools.skill"
    requires = ("tools", "skills")

    def activate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        skills = ctx.require("skills")
        assert isinstance(registry, ToolRegistry) and isinstance(skills, SkillService)
        def execute(arguments: Dict[str, object], execution: Execution) -> str:
            return skills.load(arguments.get("name"), model=True)
        registry.register(Tool(
            "skill",
            "Load the full instructions for a skill. Call it before acting on a task that names or clearly matches a skill in the session skill catalog.",
            {"type": "object", "properties": {"name": {"type": "string", "description": "The exact skill name from the available skills list."}}, "required": ["name"], "additionalProperties": False},
            execute,
            kind="skill",
        ))

    def deactivate(self, ctx: ServiceContext) -> None:
        registry = ctx.require("tools")
        assert isinstance(registry, ToolRegistry)
        registry.unregister("skill")
