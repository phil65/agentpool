"""serve-mcp: agent and team tools as MCP background tasks (SEP-2663)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp_tasks import call_tool_task
from mcp import types
from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest

from agentpool import Agent, AgentPool
from agentpool_config.pool_server import MCPPoolServerConfig
from agentpool_server.mcp_server.server import MCPServer


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastmcp.client.client import CallToolResult
    from pydantic_ai import ModelMessage


class GatedModel:
    """A model whose answers wait until the test releases them."""

    def __init__(self, *, gated: bool) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        if not gated:
            self.release.set()

    async def stream(self, messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield f"answer {self.calls}"


async def _server(
    pool: AgentPool,
    model: GatedModel,
    *,
    background_tasks: bool,
    tasks_ttl: timedelta | None = None,
) -> MCPServer:
    await pool.add_agent(Agent(name="helper", model=FunctionModel(stream_function=model.stream)))
    config = MCPPoolServerConfig(background_tasks=background_tasks, tasks_ttl=tasks_ttl)
    server = MCPServer(pool, config)
    await server._register_pool_tools()
    return server


def _text(result: CallToolResult) -> str:
    match result.content:
        case [types.TextContent(text=text), *_]:
            return text
        case _:
            raise AssertionError(f"no text content: {result}")


async def test_repeated_task_calls_run_the_agent_again() -> None:
    model = GatedModel(gated=False)
    async with AgentPool() as pool:
        server = await _server(pool, model, background_tasks=True)
        prompt = f"hi {uuid4()}"
        async with Client(server.fastmcp) as client:
            first = await client.call_tool("ask_helper", {"prompt": prompt})
            second = await client.call_tool("ask_helper", {"prompt": prompt})
    assert model.calls == 2
    assert [_text(first), _text(second)] == ["answer 1", "answer 2"]


async def test_tools_are_not_tasks_by_default() -> None:
    model = GatedModel(gated=False)
    async with AgentPool() as pool:
        server = await _server(pool, model, background_tasks=False)
        async with Client(server.fastmcp) as client:
            with pytest.raises(ToolError):
                await call_tool_task(client, "ask_helper", {"prompt": "hi"})


async def test_task_returns_before_the_run_finishes() -> None:
    model = GatedModel(gated=True)
    async with AgentPool() as pool:
        server = await _server(pool, model, background_tasks=True)
        async with Client(server.fastmcp) as client:
            task = await call_tool_task(client, "ask_helper", {"prompt": "hi"})
            await asyncio.wait_for(model.started.wait(), timeout=10)
            assert (await task.status()).status == "working"
            model.release.set()
            result = await asyncio.wait_for(task.result(), timeout=10)
    assert _text(result) == "answer 1"


async def test_cancelling_a_task_cancels_the_run() -> None:
    model = GatedModel(gated=True)
    async with AgentPool() as pool:
        server = await _server(pool, model, background_tasks=True)
        async with Client(server.fastmcp) as client:
            task = await call_tool_task(client, "ask_helper", {"prompt": "hi"})
            await asyncio.wait_for(model.started.wait(), timeout=10)
            await task.cancel()
            for _ in range(100):
                if model.cancelled:
                    break
                await asyncio.sleep(0.05)
            assert (await task.status()).status == "cancelled"
    assert model.cancelled


async def test_tasks_ttl_sets_the_task_lifetime() -> None:
    model = GatedModel(gated=False)
    async with AgentPool() as pool:
        server = await _server(pool, model, background_tasks=True, tasks_ttl=timedelta(hours=2))
        async with Client(server.fastmcp) as client:
            task = await call_tool_task(client, "ask_helper", {"prompt": "hi"})
            await asyncio.wait_for(task.result(), timeout=10)
    assert task.create_result.ttl_ms == 2 * 60 * 60 * 1000
