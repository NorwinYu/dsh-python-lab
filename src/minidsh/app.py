from typing import Callable, Optional

from .agent import Agent
from .approval import ApprovalPlugin, ApprovalService
from .config import AppConfig
from .deepseek import DeepSeekModelPlugin
from .events import EventBus
from .jobs import JobPlugin
from .llm import MockModelPlugin, Model
from .observations import ObservationPlugin
from .plugins import Plugin, PluginManager, ServiceContext
from .sandbox import SandboxPolicy, SandboxPolicyPlugin
from .session import JsonlSessionPlugin, SessionStore
from .shell import BashToolPlugin, JobToolsPlugin, make_bash_tool
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
        assert isinstance(model, Model)
        assert isinstance(tools, ToolRegistry)
        assert isinstance(prompt, SystemPrompt)
        assert isinstance(sandbox, SandboxPolicy)
        assert isinstance(approval, ApprovalService)
        assert session is None or isinstance(session, SessionStore)
        runtime_context = prompt.runtime_context(self.config.workspace, sandbox.context(), approval.context())
        agent = Agent(
            model=model,
            tools=tools,
            system_prompt=prompt,
            runtime_context=runtime_context,
            events=ctx.events,
            session=session,
        )
        ctx.provide("agent", agent, self.name)


class Application:
    def __init__(self, config: AppConfig, approval_answerer: Optional[Callable[[str], str]] = None) -> None:
        self.config = config
        self.events = EventBus()
        self.ctx = ServiceContext(self.events)
        self.manager = PluginManager(self.ctx, config.features)
        answerer = approval_answerer
        if config.approve_all:
            answerer = lambda reason: "allowed-once"

        self.manager.register(SystemPromptPlugin())
        self.manager.register(SandboxPolicyPlugin(config.sandbox_mode, config.workspace))
        self.manager.register(ApprovalPlugin(config.resolved_approval_policy(), answerer))
        self.manager.register(ObservationPlugin())
        self.manager.register(JobPlugin(config.session_dir.parent / "jobs"))
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
        self.manager.register(JobToolsPlugin(("tool:jobs", 1600, JOBS_GUIDANCE)))
        self.manager.register(JsonlSessionPlugin(config.session_dir))
        self.manager.register(MockModelPlugin())
        self.manager.register(DeepSeekModelPlugin())
        self.manager.register(AgentPlugin(config))
        self.manager.activate_all()

    @property
    def agent(self) -> Agent:
        agent = self.ctx.require("agent")
        assert isinstance(agent, Agent)
        return agent

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
