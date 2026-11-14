from typing import Callable, Dict, Optional

from .agent import Agent
from .approval import ApprovalPlugin, ApprovalService
from .compaction import CompactionPlugin, CompactionService
from .config import AppConfig
from .deepseek import DeepSeekModelPlugin
from .events import EventBus
from .goals import GoalPlugin, GoalRoundDriver, GoalService, GoalToolPlugin
from .jobs import JobPlugin
from .llm import MockModelPlugin, Model
from .mcp import McpPlugin, McpResourcesPlugin
from .observations import ObservationPlugin, ObservationStore
from .plugins import DisabledExternalPlugin, Plugin, PluginManager, ServiceContext
from .pty_shell import PersistentBashPlugin, PtyPlugin
from .pruner import ToolResultPrunerPlugin
from .questions import AskUserPlugin, UserQuestionsPlugin
from .todo import TodoPlugin
from .web_tools import WebPlugin, WebToolPlugin
from .workflow import WorkflowPlugin, WorkflowToolPlugin
from .sandbox import SandboxPolicy, SandboxPolicyPlugin
from .schedule import SchedulePlugin, ScheduleService, ScheduleToolPlugin
from .seatbelt import SeatbeltPlugin
from .session import JsonlSessionPlugin, SessionStore
from .shell import BashToolPlugin, JobToolsPlugin, make_bash_tool
from .skills import SkillService, SkillsPlugin, SkillToolPlugin
from .subagents import SubagentControlPlugin, SubagentPlugin, SubagentService, SubagentToolPlugin
from .system_prompt import (
    BASH_GUIDANCE,
    EDIT_GUIDANCE,
    GLOB_GUIDANCE,
    JOBS_GUIDANCE,
    READ_GUIDANCE,
    WRITE_GUIDANCE,
    SystemPrompt,
    SystemPromptPlugin,
)
from .tools import CoreToolsPlugin, SingleToolPlugin, ToolRegistry, make_edit_tool, make_glob_tool, make_read_tool, make_write_tool


class AgentPlugin(Plugin):
    name = "agent.core"
    provides = ("agent",)
    requires = ("llm", "tools", "systemPrompt", "sandboxPolicy", "approval")

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def activate(self, ctx: ServiceContext) -> None:
        model = ctx.require("llm")
        tools = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        sandbox = ctx.require("sandboxPolicy")
        approval = ctx.require("approval")
        session = ctx.optional("session")
        observations = ctx.optional("observations")
        compaction = ctx.optional("compaction")
        subagents = ctx.optional("subagents")
        skills = ctx.optional("skills")
        schedules = ctx.optional("schedule")
        goals = ctx.optional("goals")
        assert isinstance(model, Model)
        assert isinstance(tools, ToolRegistry)
        assert isinstance(prompt, SystemPrompt)
        assert isinstance(sandbox, SandboxPolicy)
        assert isinstance(approval, ApprovalService)
        assert session is None or isinstance(session, SessionStore)
        assert observations is None or isinstance(observations, ObservationStore)
        assert compaction is None or isinstance(compaction, CompactionService)
        assert subagents is None or isinstance(subagents, SubagentService)
        assert skills is None or isinstance(skills, SkillService)
        assert schedules is None or isinstance(schedules, ScheduleService)
        assert goals is None or isinstance(goals, GoalService)
        runtime_context = prompt.runtime_context(self.config.workspace, sandbox.context(), approval.context())
        agent = Agent(
            model=model,
            tools=tools,
            system_prompt=prompt,
            runtime_context=runtime_context,
            events=ctx.events,
            session=session,
            observations=observations,
            compaction=compaction,
            workspace=self.config.workspace,
            allow_outside=sandbox.mode == "danger-full-access",
            streaming=self.config.features["core.streaming"],
            record_stream=self.config.features["core.stream-recording"],
            sandbox_policy=sandbox,
            approval_service=approval,
            subagents=subagents,
            skills=skills,
            schedules=schedules,
            goals=goals,
        )
        ctx.provide("agent", agent, self.name)


class Application:
    def __init__(
        self,
        config: AppConfig,
        approval_answerer: Optional[Callable[[str], str]] = None,
        question_answerer: Optional[Callable[[Dict[str, object]], Dict[str, object]]] = None,
    ) -> None:
        self.config = config
        self.events = EventBus()
        self.ctx = ServiceContext(self.events)
        self.manager = PluginManager(self.ctx, config.features)
        answerer = approval_answerer
        if config.approve_all:
            answerer = lambda reason: "allowed-once"

        self.manager.register(SystemPromptPlugin())
        self.manager.register(SeatbeltPlugin(config.workspace, config.session_dir.parent / "jobs"))
        self.manager.register(SandboxPolicyPlugin(config.sandbox_mode, config.workspace))
        self.manager.register(PtyPlugin(config.workspace))
        self.manager.register(ApprovalPlugin(config.resolved_approval_policy(), answerer))
        self.manager.register(UserQuestionsPlugin(question_answerer))
        self.manager.register(ObservationPlugin())
        self.manager.register(JobPlugin(config.session_dir.parent / "jobs"))
        self.manager.register(WebPlugin())
        self.manager.register(CoreToolsPlugin())
        self.manager.register(SingleToolPlugin("tools.read", make_read_tool(config.workspace), ("tool:read", 1100, READ_GUIDANCE)))
        write_guidance = WRITE_GUIDANCE
        if config.features["tools.edit"]:
            write_guidance += " and prefer edit for targeted changes"
        write_guidance += "."
        self.manager.register(SingleToolPlugin("tools.write", make_write_tool(config.workspace), ("tool:write", 1200, write_guidance)))
        self.manager.register(SingleToolPlugin("tools.edit", make_edit_tool(config.workspace), ("tool:edit", 1300, EDIT_GUIDANCE)))
        self.manager.register(SingleToolPlugin("tools.glob", make_glob_tool(config.workspace), ("tool:glob", 1400, GLOB_GUIDANCE)))
        self.manager.register(BashToolPlugin("tools.bash", make_bash_tool(config.workspace), ("tool:bash", 1000, BASH_GUIDANCE)))
        self.manager.register(PersistentBashPlugin())
        self.manager.register(JobToolsPlugin(("tool:jobs", 1600, JOBS_GUIDANCE)))
        self.manager.register(AskUserPlugin(config.features["tools.ask-user-timed"]))
        self.manager.register(TodoPlugin())
        self.manager.register(WebToolPlugin("tools.web-fetch", "web_fetch"))
        self.manager.register(WebToolPlugin("tools.web-search", "web_search", config.features["tools.web-fetch"]))
        self.manager.register(JsonlSessionPlugin(config.session_dir))
        self.manager.register(SubagentPlugin())
        self.manager.register(SubagentToolPlugin())
        self.manager.register(SubagentToolPlugin("tools.subagent-fork", "fork"))
        self.manager.register(SubagentControlPlugin())
        self.manager.register(WorkflowPlugin())
        self.manager.register(WorkflowToolPlugin())
        self.manager.register(McpResourcesPlugin())
        self.manager.register(McpPlugin(config.workspace, config.mcp_servers))
        self.manager.register(SkillsPlugin(config.workspace, config.skill_dirs, config.skill_include_default_roots, config.features["tools.skill"]))
        self.manager.register(SkillToolPlugin())
        self.manager.register(SchedulePlugin(config.session_dir.parent / "schedule.json"))
        self.manager.register(ScheduleToolPlugin())
        self.manager.register(GoalPlugin())
        self.manager.register(GoalToolPlugin())
        self.manager.register(GoalRoundDriver())
        self.manager.register(MockModelPlugin())
        self.manager.register(
            DeepSeekModelPlugin(
                retry_enabled=config.features["llm.retry"],
                listing_enabled=config.features["llm.model-listing"],
                files_enabled=config.features["llm.files"],
                workspace=config.workspace,
                allow_outside=config.sandbox_mode == "danger-full-access",
            )
        )
        self.manager.register(CompactionPlugin(config.context_window))
        self.manager.register(ToolResultPrunerPlugin())
        self.manager.register(AgentPlugin(config))
        for spec in config.plugin_specs:
            if config.features[spec.name]:
                self.manager.register(
                    spec.load(),
                    origin="entry-point",
                    distribution=spec.distribution,
                    version=spec.version,
                    value=spec.value,
                )
            else:
                self.manager.register(
                    DisabledExternalPlugin(spec.name),
                    origin="entry-point",
                    distribution=spec.distribution,
                    version=spec.version,
                    value=spec.value,
                )
        self.manager.activate_all()

    @property
    def agent(self) -> Agent:
        agent = self.ctx.require("agent")
        assert isinstance(agent, Agent)
        return agent

    @property
    def model(self) -> Model:
        model = self.ctx.require("llm")
        assert isinstance(model, Model)
        return model

    @property
    def tools(self) -> ToolRegistry:
        tools = self.ctx.require("tools")
        assert isinstance(tools, ToolRegistry)
        return tools

    @property
    def system_prompt(self) -> SystemPrompt:
        prompt = self.ctx.require("systemPrompt")
        assert isinstance(prompt, SystemPrompt)
        return prompt

    @property
    def runtime_context(self) -> str:
        sandbox = self.ctx.require("sandboxPolicy")
        approval = self.ctx.require("approval")
        assert isinstance(sandbox, SandboxPolicy)
        assert isinstance(approval, ApprovalService)
        return self.system_prompt.runtime_context(self.config.workspace, sandbox.context(), approval.context())

    @property
    def session(self) -> Optional[SessionStore]:
        session = self.ctx.optional("session")
        assert session is None or isinstance(session, SessionStore)
        return session

    def close(self) -> None:
        self.manager.deactivate_all()
