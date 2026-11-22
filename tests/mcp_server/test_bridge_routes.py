"""The tool bridge routes each session's tool calls to the turn running on its route."""

from __future__ import annotations

from dataclasses import replace

from fastmcp import Client
from pydantic_ai.models.test import TestModel

from agentpool import Agent, AgentContext
from agentpool.mcp_server.tool_bridge import ToolManagerBridge
from agentpool.sessions import Session


async def _text(url: str, tool: str) -> str:
    async with Client(url) as client:
        result = await client.call_tool(tool, {})
    return "".join(block.text for block in result.content if block.type == "text")


async def test_tool_calls_run_with_the_context_of_their_route() -> None:
    async def whoami(ctx: AgentContext) -> str:
        """Name the session whose turn this call belongs to."""
        return str(ctx.data)

    agent = Agent(name="bridged", model=TestModel(), tools=[whoami])
    async with agent, ToolManagerBridge(node=agent) as bridge:
        first, second = Session.create("bridged"), Session.create("bridged")
        a = replace(agent.get_context(data="A"), runtime=agent.runtime(first))
        b = replace(agent.get_context(data="B"), runtime=agent.runtime(second))
        async with (
            bridge.set_run_context(a, route=first.session_id),
            bridge.set_run_context(b, route=second.session_id),
        ):
            assert await _text(bridge.url_for(first.session_id), "whoami") == "A"
            assert await _text(bridge.url_for(second.session_id), "whoami") == "B"
            # The plain URL is the default session's: no turn runs on it
            assert await _text(bridge.url, "whoami") == "None"
        assert bridge.context(first.session_id) is None  # Cleared when the turn ends


async def test_injections_reach_the_tool_calls_of_their_own_session() -> None:
    async def work() -> str:
        """Do some work."""
        return "done"

    agent = Agent(name="bridged", model=TestModel(), tools=[work])
    async with agent, ToolManagerBridge(node=agent) as bridge:
        first, second = Session.create("bridged"), Session.create("bridged")
        agent.runtime(first).injections.inject("FOR FIRST")
        a = replace(agent.get_context(), runtime=agent.runtime(first))
        b = replace(agent.get_context(), runtime=agent.runtime(second))
        async with (
            bridge.set_run_context(a, route=first.session_id),
            bridge.set_run_context(b, route=second.session_id),
        ):
            assert "FOR FIRST" not in await _text(bridge.url_for(second.session_id), "work")
            assert "FOR FIRST" in await _text(bridge.url_for(first.session_id), "work")


async def test_tool_metadata_is_kept_per_route() -> None:
    from agentpool.tools.base import ToolResult

    async def tagged(ctx: AgentContext) -> ToolResult:
        """Return a result with metadata."""
        return ToolResult(content="ok", metadata={"by": str(ctx.data)})

    agent = Agent(name="bridged", model=TestModel(), tools=[tagged])
    async with agent, ToolManagerBridge(node=agent) as bridge:
        session = Session.create("bridged")
        ctx = agent.get_context(data="S")
        async with bridge.set_run_context(ctx, route=session.session_id) as route:
            await _text(bridge.url_for(session.session_id), "tagged")
        assert [m["by"] for m in route.tool_metadata.values()] == ["S"]
        assert bridge.tool_metadata == {}  # The default route's
