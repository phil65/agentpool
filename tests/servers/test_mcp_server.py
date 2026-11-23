"""serve-mcp serves agent tools without caching their results."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from fastmcp import Client
from mcp import types
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agentpool import Agent, AgentPool
from agentpool_config.pool_server import MCPPoolServerConfig
from agentpool_server.mcp_server.server import MCPServer


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastmcp.client.client import CallToolResult
    from pydantic_ai import ModelMessage


def _text(result: CallToolResult) -> str:
    match result.content:
        case [types.TextContent(text=text), *_]:
            return text
        case _:
            raise AssertionError(f"no text content: {result}")


async def test_repeated_calls_run_the_agent_again() -> None:
    calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        yield f"answer {calls}"

    async with AgentPool() as pool:
        await pool.add_agent(Agent(name="helper", model=FunctionModel(stream_function=stream)))
        server = MCPServer(pool, MCPPoolServerConfig())
        await server._register_pool_tools()
        prompt = f"hi {uuid4()}"
        async with Client(server.fastmcp) as client:
            first = await client.call_tool("ask_helper", {"prompt": prompt})
            second = await client.call_tool("ask_helper", {"prompt": prompt})
    assert calls == 2
    assert [_text(first), _text(second)] == ["answer 1", "answer 2"]


async def _tool_names(serve_nodes: list[str] | bool) -> list[str]:
    async with AgentPool() as pool:
        for name in ("a", "b"):
            await pool.add_agent(Agent(name, model=FunctionModel(stream_function=_echo)))
        server = MCPServer(pool, MCPPoolServerConfig(serve_nodes=serve_nodes))
        await server._register_pool_tools()
        async with Client(server.fastmcp) as client:
            return sorted(tool.name for tool in await client.list_tools())


async def _echo(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    yield "echo"


async def test_serve_nodes_picks_the_served_agents() -> None:
    assert await _tool_names(True) == ["ask_a", "ask_b"]
    assert await _tool_names(["b"]) == ["ask_b"]
    assert await _tool_names(False) == []
