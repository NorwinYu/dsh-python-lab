from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .plugins import Plugin, ServiceContext
from .session import SessionStore
from .system_prompt import PromptSection, SystemPrompt
from .tools import ToolRegistry


PLUGIN_TOOLS: Dict[str, Tuple[str, ...]] = {
    "tools.read": ("read",),
    "tools.write": ("write",),
    "tools.edit": ("edit",),
    "tools.glob": ("glob",),
    "tools.bash": ("bash",),
    "tools.bash-persistent": ("bash",),
    "tools.jobs": ("job_output", "job_list", "job_kill"),
    "tools.ask-user": ("ask_user_question",),
    "tools.todo": ("todo_write",),
    "tools.web-fetch": ("web_fetch",),
    "tools.web-search": ("web_search",),
    "tools.subagent": ("subagent",),
    "tools.subagent-fork": ("subagent_fork",),
    "tools.subagent-control": ("send_message", "interrupt_agent", "list_agents", "wait_agent"),
    "tools.workflow": ("workflow",),
    "tools.mcp-resources": ("list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource"),
    "tools.skill": ("skill",),
    "tools.schedule": ("schedule_create", "schedule_list", "schedule_view", "schedule_edit"),
    "tools.goal": ("get_goal", "create_goal", "update_goal"),
    "tools.run-code": ("run_code",),
}
PROMPT_PLUGINS = {
    "tool:bash": "tools.bash",
    "tool:read": "tools.read",
    "tool:write": "tools.write",
    "tool:edit": "tools.edit",
    "tool:glob": "tools.glob",
    "tool:jobs": "tools.jobs",
    "tool:ask_user_question": "tools.ask-user",
    "tool:todo_write": "tools.todo",
    "tool:web_fetch": "tools.web-fetch",
    "tool:web_search": "tools.web-search",
    "tool:subagent": "tools.subagent",
    "tool:subagent_fork": "tools.subagent-fork",
    "tool:subagent-control": "tools.subagent-control",
    "tool:workflow": "tools.workflow",
    "tool:skill": "tools.skill",
    "tool:schedule": "tools.schedule",
    "tool:goal": "tools.goal",
}
PRESET_SERVICES = {"core.mcp", "core.skills", "core.schedule", "core.goals", "core.subagents", "core.workflow", "core.ptc"}


@dataclass
class PresetComposition:
    preset_id: str
    tools: ToolRegistry
    prompt: SystemPrompt
    complete: bool
    include_runtime_context: bool


class PresetService:
    def __init__(self, ctx: ServiceContext, session: SessionStore, definitions: Dict[str, Dict[str, object]], default: str) -> None:
        self.ctx = ctx
        self.session = session
        self._definitions: Dict[str, Dict[str, object]] = {}
        for preset_id, definition in definitions.items():
            if not preset_id or not isinstance(definition, dict) or not isinstance(definition.get("plugins"), list) or any(not isinstance(plugin, str) for plugin in definition["plugins"]):
                raise ValueError(f"invalid agent preset: {preset_id}")
            unknown = set(definition["plugins"]) - set(PLUGIN_TOOLS) - PRESET_SERVICES - {"persona"}
            if unknown:
                raise ValueError(f"unknown preset plugins: {', '.join(sorted(unknown))}")
            self._definitions[preset_id] = dict(definition)
        if default not in self._definitions:
            raise ValueError(f"unknown default agent preset: {default}")
        self.default = default

    def list(self) -> List[Dict[str, object]]:
        rows = []
        for preset_id, definition in self._definitions.items():
            rows.append({
                "id": preset_id,
                "name": definition.get("name"),
                "description": definition.get("description"),
                "order": definition.get("order"),
                "plugins": list(definition["plugins"]),
                "persona": definition.get("persona"),
            })
        return sorted(rows, key=lambda row: (row["order"] is None, row["order"] if row["order"] is not None else 0, row["id"]))

    def selected(self, session_id: str) -> Optional[str]:
        selected: Optional[str] = None
        for event in self.session.load(session_id):
            if event["type"] == "preset/selected":
                preset_id = event["payload"].get("id")
                if not isinstance(preset_id, str):
                    raise ValueError("invalid preset selection")
                selected = preset_id
        return selected

    def select(self, session_id: str, preset_id: str) -> Dict[str, object]:
        if preset_id not in self._definitions:
            raise ValueError(f"unknown agent preset: {preset_id}")
        if self.session.latest_turn(session_id) > 0:
            raise ValueError("preset can only be changed before the first model turn")
        if self.selected(session_id) == preset_id:
            return {"id": preset_id, "changed": False}
        self.session.append(session_id, "preset/selected", {"id": preset_id})
        self.ctx.events.emit("tools/change", {"session_id": session_id, "preset": preset_id})
        return {"id": preset_id, "changed": True}

    def inherit(self, parent_id: str, child_id: str) -> None:
        selected = self.selected(parent_id)
        if selected is not None and self.selected(child_id) is None:
            self.session.append(child_id, "preset/selected", {"id": selected})

    def bind(self, session_id: str) -> PresetComposition:
        selected = self.selected(session_id)
        if selected is None:
            selected = self.default
            self.session.append(session_id, "preset/selected", {"id": selected})
        if selected not in self._definitions:
            raise ValueError(f"session preset is no longer defined: {selected}")
        return self._compose(selected)

    def _compose(self, preset_id: str) -> PresetComposition:
        definition = self._definitions[preset_id]
        parent_tools = self.ctx.require("tools")
        parent_prompt = self.ctx.require("systemPrompt")
        assert isinstance(parent_tools, ToolRegistry) and isinstance(parent_prompt, SystemPrompt)
        scoped = ServiceContext(self.ctx.events)
        scoped._services = dict(self.ctx._services)
        scoped._owners = dict(self.ctx._owners)
        prompt = SystemPrompt()
        scoped._services["systemPrompt"] = prompt
        scoped._owners["systemPrompt"] = f"preset:{preset_id}"
        registry = ToolRegistry(scoped)
        scoped._services["tools"] = registry
        scoped._owners["tools"] = f"preset:{preset_id}"
        requested_tools = {name for plugin in definition["plugins"] if plugin in PLUGIN_TOOLS for name in PLUGIN_TOOLS[plugin]}
        for tool in parent_tools.snapshot():
            if tool.name in requested_tools:
                registry.register(tool)
        requested_prompts = {name for name, plugin in PROMPT_PLUGINS.items() if plugin in definition["plugins"]}
        for section in parent_prompt.snapshot():
            if section.name != "harness:identity" and (section.name in requested_prompts or section.name.startswith("mcp:") and "core.mcp" in definition["plugins"] or section.name == "mcp:resource-servers" and "tools.mcp-resources" in definition["plugins"]):
                prompt.register(section.name, section.order, section.text)
        persona = definition.get("persona", {})
        if not isinstance(persona, dict):
            raise ValueError(f"invalid preset persona: {preset_id}")
        complete = persona.get("complete", False) is True
        include_runtime = persona.get("includeRuntimeContext", True) is not False
        prefix = persona.get("prefix")
        if prefix is not None and not isinstance(prefix, str):
            raise ValueError(f"invalid persona prefix: {preset_id}")
        suffix = persona.get("suffix", "")
        if not isinstance(suffix, str):
            raise ValueError(f"invalid persona suffix: {preset_id}")
        if prefix is not None:
            prompt.register("deployment:persona-prefix", 0, prefix)
        if suffix:
            prompt.register("deployment:persona-suffix", 10200, suffix)
        return PresetComposition(preset_id, registry, prompt, complete, include_runtime)


class PresetPlugin(Plugin):
    name = "core.agent-presets"
    provides = ("agentPresets",)
    requires = ("session", "tools", "systemPrompt")

    def __init__(self, definitions: Dict[str, Dict[str, object]], default: str) -> None:
        self.definitions = definitions
        self.default = default

    def activate(self, ctx: ServiceContext) -> None:
        session = ctx.require("session")
        assert isinstance(session, SessionStore)
        ctx.provide("agentPresets", PresetService(ctx, session, self.definitions, self.default), self.name)
