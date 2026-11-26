"""Agents served over MCP ask the client's user, on either protocol era, never stdin."""

import asyncio
from typing import Any

from fastmcp import Client
from mcp import types
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentContext, AgentPool  # Tools' annotations resolve at runtime
from agentpool.tools.base import FunctionTool
from agentpool_config.pool_server import MCPPoolServerConfig
from agentpool_server.mcp_server.server import MCPServer


async def _served(calls: list[str]) -> tuple[AgentPool, MCPServer]:
    async def delete_files() -> str:
        """Delete the build files."""
        calls.append("deleted")
        return "deleted"

    tool = FunctionTool.from_callable(delete_files, requires_confirmation=True)
    model = TestModel(call_tools=["delete_files"], custom_output_text="done")
    pool = AgentPool()
    await pool.__aenter__()
    await pool.add_agent(Agent("cleaner", model=model, tools=[tool]))
    server = MCPServer(pool, MCPPoolServerConfig())
    await server._register_pool_tools()
    return pool, server


def _answering(decision: str, asked: list[str], note: str | None = None) -> Any:
    async def handler(
        message: str, response_type: Any, params: types.ElicitRequestParams, context: Any
    ) -> types.ElicitResult:
        asked.append(message)
        content: dict[str, Any] = {"decision": decision}
        if note is not None:
            content["message"] = note
        return types.ElicitResult(action="accept", content=content)

    return handler


def _text(result: Any) -> str:
    match result.content:
        case [types.TextContent(text=text), *_]:
            return text
        case _:
            raise AssertionError(result)


@pytest.mark.parametrize("mode", ["legacy", "auto"], ids=["handshake-era", "2026-07-28"])
async def test_allowed_tool_runs(mode: str) -> None:
    calls: list[str] = []
    asked: list[str] = []
    pool, server = await _served(calls)
    try:
        handler = _answering("allow", asked)
        async with Client(server.fastmcp, mode=mode, elicitation_handler=handler) as client:
            result = await client.call_tool("ask_cleaner", {"prompt": "clean up"})
    finally:
        await pool.__aexit__(None, None, None)
    assert len(asked) == 1
    assert "delete_files" in asked[0]
    assert calls == ["deleted"]
    assert _text(result) == "done"


@pytest.mark.parametrize("mode", ["legacy", "auto"], ids=["handshake-era", "2026-07-28"])
async def test_denied_tool_does_not_run_and_the_agent_goes_on(mode: str) -> None:
    calls: list[str] = []
    pool, server = await _served(calls)
    try:
        handler = _answering("deny", [], note="keep the cache")
        async with Client(server.fastmcp, mode=mode, elicitation_handler=handler) as client:
            result = await client.call_tool("ask_cleaner", {"prompt": "clean up"})
    finally:
        await pool.__aexit__(None, None, None)
    assert calls == []
    assert _text(result) == "done"


async def test_stopping_ends_the_served_run() -> None:
    calls: list[str] = []
    pool, server = await _served(calls)
    try:
        handler = _answering("stop", [])
        async with Client(server.fastmcp, elicitation_handler=handler) as client:
            result = await client.call_tool("ask_cleaner", {"prompt": "clean up"})
    finally:
        await pool.__aexit__(None, None, None)
    assert calls == []
    assert _text(result) != "done"  # Ended before the agent's final answer


async def test_allowing_for_the_session_is_kept_by_the_session() -> None:
    calls: list[str] = []
    pool, server = await _served(calls)
    try:
        handler = _answering("allow_session", [])
        async with Client(server.fastmcp, elicitation_handler=handler) as client:
            await client.call_tool("ask_cleaner", {"prompt": "clean up"})
        kept = pool.get_agent("cleaner").session.overrides.tool_decisions
    finally:
        await pool.__aexit__(None, None, None)
    assert calls == ["deleted"]
    assert kept == {"delete_files": "approve"}


async def test_a_client_that_cannot_answer_denies_the_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_stdin(*args: object) -> str:
        raise AssertionError("asked on stdin")

    monkeypatch.setattr("builtins.input", no_stdin)
    calls: list[str] = []
    pool, server = await _served(calls)
    try:
        async with Client(server.fastmcp, mode="legacy") as client:  # No elicitation support
            result = await client.call_tool("ask_cleaner", {"prompt": "clean up"})
    finally:
        await pool.__aexit__(None, None, None)
    assert calls == []
    assert _text(result) == "done"


async def test_an_unanswered_question_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp import InputRequiredRoundsExceededError

    from agentpool_server.mcp_server import asking

    monkeypatch.setattr(asking, "PENDING_TTL", 0.2)
    calls: list[str] = []
    pool, server = await _served(calls)
    try:
        # A client that never answers: the run waits on its question, then is cancelled
        async with Client(server.fastmcp, input_required_max_rounds=0) as client:
            with pytest.raises(InputRequiredRoundsExceededError):
                await client.call_tool("ask_cleaner", {"prompt": "clean up"})
            assert len(server.runs._waiting) == 1
            await asyncio.sleep(0.5)
            assert server.runs._waiting == {}
    finally:
        await pool.__aexit__(None, None, None)
    assert calls == []


@pytest.mark.parametrize("mode", ["legacy", "auto"], ids=["handshake-era", "2026-07-28"])
async def test_served_runs_report_their_progress(mode: str) -> None:
    async def build(ctx: AgentContext) -> str:
        """Build the project."""
        await ctx.report_progress(1, 2, "compiling")
        return "built"

    model = TestModel(call_tools=["build"], custom_output_text="done")
    pool = AgentPool()
    await pool.__aenter__()
    reported: list[tuple[float, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        reported.append((progress, message))

    try:
        await pool.add_agent(Agent("builder", model=model, tools=[build]))
        server = MCPServer(pool, MCPPoolServerConfig())
        await server._register_pool_tools()
        async with Client(server.fastmcp, mode=mode, progress_handler=on_progress) as client:
            result = await client.call_tool("ask_builder", {"prompt": "build it"})
    finally:
        await pool.__aexit__(None, None, None)
    assert _text(result) == "done"
    messages = [message for _, message in reported]
    assert messages[0] is not None
    assert messages[0].startswith("builder: ")
    assert "build" in messages[0]
    assert messages[1:] == ["builder: compiling"]
    assert [progress for progress, _ in reported] == [1, 2]


async def test_shutdown_cancels_the_runs_waiting_for_an_answer() -> None:
    """#61.3b: a waiting served run doesn't outlive the server."""
    from mcp import InputRequiredRoundsExceededError

    calls: list[str] = []
    pool, server = await _served(calls)
    try:
        async with Client(server.fastmcp, input_required_max_rounds=0) as client:
            with pytest.raises(InputRequiredRoundsExceededError):
                await client.call_tool("ask_cleaner", {"prompt": "clean up"})
        [run] = server.runs._waiting.values()
        await server.shutdown()
        assert server.runs._waiting == {}
        assert run.task is not None
        await asyncio.wait({run.task}, timeout=5)
        assert run.task.done()
    finally:
        await pool.__aexit__(None, None, None)
    assert calls == []
