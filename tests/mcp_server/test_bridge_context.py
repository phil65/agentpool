"""Tool bridge: argument validation and context injection (#49.4), toolset cleanup (#49.5)."""

from __future__ import annotations

import asyncio
from typing import Any, Self
from unittest.mock import AsyncMock

import fastmcp
from pydantic_ai.models.test import TestModel

from agentpool import Agent, AgentPool
from agentpool.agents.context import AgentContext
from agentpool.mcp_server.tool_bridge import ToolManagerBridge
from agentpool.resource_providers import ResourceProvider
from agentpool_toolsets.mcp_discovery.toolset import MCPDiscoveryToolset


TIMEOUT = 30
seen: list[Any] = []


async def typed_tool(ctx: AgentContext, count: int) -> str:
    """Count things."""
    seen.append(ctx)
    return f"count={count}"


async def test_bridge_validates_arguments() -> None:
    """#49.4a: wrong arguments are an error result, not passed to the tool."""
    seen.clear()
    async with asyncio.timeout(TIMEOUT), AgentPool() as pool:
        agent = Agent(name="test", model=TestModel())
        await pool.add_agent(agent)
        agent.tools.register_tool(typed_tool)
        async with (
            ToolManagerBridge(node=agent) as bridge,
            fastmcp.Client(bridge.url, auth=bridge.token) as client,
        ):
            result = await client.call_tool("typed_tool", {"count": "many"}, raise_on_error=False)
            assert result.is_error
            assert "invalid args" in str(result.content)
            assert not seen
            ok = await client.call_tool("typed_tool", {"count": 3})
            assert ok.content[0].text == "count=3"  # type: ignore[union-attr]


async def test_bridge_injects_context_over_caller_values_and_hides_it_from_hooks() -> None:
    """#49.4b/c: a caller can't replace the context; hooks get the arguments only."""
    seen.clear()
    async with asyncio.timeout(TIMEOUT), AgentPool() as pool:
        agent = Agent(name="test", model=TestModel())
        await pool.add_agent(agent)
        agent.tools.register_tool(typed_tool)
        hooks = AsyncMock()
        hooks.run_pre_tool_hooks.return_value = {}
        agent.hooks = hooks
        async with ToolManagerBridge(node=agent) as bridge:
            tool = await agent.tools.get_tool("typed_tool")
            ctx = agent.get_context()
            kwargs = {"count": 1, "ctx": "forged"}
            await bridge.invoke_tool_with_context(tool, ctx, kwargs)
    assert seen == [ctx]
    post = hooks.run_post_tool_hooks.await_args
    assert post is not None
    assert post.kwargs["tool_input"] == {"count": 1, "ctx": "forged"}
    assert not any(isinstance(v, AgentContext) for v in post.kwargs["tool_input"].values())


class _Toolset(ResourceProvider):
    def __init__(self) -> None:
        super().__init__(name="tracked")
        self.entered = self.exited = False

    async def __aenter__(self) -> Self:
        self.entered = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


async def test_agent_enters_and_exits_its_toolsets() -> None:
    """#49.5: toolsets get their lifecycle (MCPDiscoveryToolset cleans up on exit)."""
    toolset = _Toolset()
    async with Agent(name="test", model=TestModel(), toolsets=[toolset]):
        assert toolset.entered
        assert not toolset.exited
    assert toolset.exited


async def test_discovery_toolset_cleans_up_on_exit() -> None:
    toolset = MCPDiscoveryToolset(name="discovery")
    toolset.cleanup = AsyncMock()  # type: ignore[method-assign]
    async with toolset:
        pass
    toolset.cleanup.assert_awaited_once()
