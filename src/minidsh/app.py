from typing import Optional

from .agent import Agent
from .config import AppConfig
from .events import EventBus
from .llm import MockModelPlugin, Model
from .plugins import Plugin, PluginManager, ServiceContext
from .session import JsonlSessionPlugin, SessionStore
from .tools import CoreToolsPlugin, SingleToolPlugin, ToolRegistry, make_list_dir_tool, make_read_file_tool


class AgentPlugin(Plugin):
    name = "agent.core"
    provides = ("agent",)
    requires = ("llm", "tools")

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def activate(self, ctx: ServiceContext) -> None:
        model = ctx.require("llm")
        tools = ctx.require("tools")
        session = ctx.optional("session")
        assert isinstance(model, Model)
        assert isinstance(tools, ToolRegistry)
        assert session is None or isinstance(session, SessionStore)
        agent = Agent(
            model=model,
            tools=tools,
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
        self.manager.register(CoreToolsPlugin())
        self.manager.register(SingleToolPlugin("tools.list_dir", make_list_dir_tool(config.workspace)))
        self.manager.register(SingleToolPlugin("tools.read_file", make_read_file_tool(config.workspace)))
        self.manager.register(JsonlSessionPlugin(config.session_dir))
        self.manager.register(MockModelPlugin())
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
    def session(self) -> Optional[SessionStore]:
        session = self.ctx.optional("session")
        assert session is None or isinstance(session, SessionStore)
        return session

    def close(self) -> None:
        self.manager.deactivate_all()
