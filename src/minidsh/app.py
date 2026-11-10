from typing import Optional

from .agent import Agent
from .config import AppConfig
from .deepseek import DeepSeekModelPlugin
from .events import EventBus
from .llm import MockModelPlugin, Model
from .plugins import Plugin, PluginManager, ServiceContext
from .session import JsonlSessionPlugin, SessionStore
from .system_prompt import GLOB_GUIDANCE, READ_GUIDANCE, SystemPrompt, SystemPromptPlugin
from .tools import CoreToolsPlugin, SingleToolPlugin, ToolRegistry, make_glob_tool, make_read_tool


class AgentPlugin(Plugin):
    name = "agent.core"
    provides = ("agent",)
    requires = ("llm", "tools", "systemPrompt")

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def activate(self, ctx: ServiceContext) -> None:
        model = ctx.require("llm")
        tools = ctx.require("tools")
        prompt = ctx.require("systemPrompt")
        session = ctx.optional("session")
        assert isinstance(model, Model)
        assert isinstance(tools, ToolRegistry)
        assert isinstance(prompt, SystemPrompt)
        assert session is None or isinstance(session, SessionStore)
        agent = Agent(
            model=model,
            tools=tools,
            system_prompt=prompt,
            workspace=self.config.workspace,
            events=ctx.events,
            session=session,
        )
        ctx.provide("agent", agent, self.name)


class Application:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.events = EventBus()
        self.ctx = ServiceContext(self.events)
        self.manager = PluginManager(self.ctx, config.features)
        self.manager.register(SystemPromptPlugin())
        self.manager.register(CoreToolsPlugin())
        self.manager.register(SingleToolPlugin("tools.glob", make_glob_tool(config.workspace), ("tool:glob", 1400, GLOB_GUIDANCE)))
        self.manager.register(SingleToolPlugin("tools.read", make_read_tool(config.workspace), ("tool:read", 1100, READ_GUIDANCE)))
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
    def session(self) -> Optional[SessionStore]:
        session = self.ctx.optional("session")
        assert session is None or isinstance(session, SessionStore)
        return session

    def close(self) -> None:
        self.manager.deactivate_all()
