"""MCP connection lifecycle: server processes, failures, timeouts, signals, elicitation."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, Mock

from mcp.types import ElicitResult
import pytest

from agentpool.mcp_server.client import MCPClient
from agentpool.mcp_server.manager import MCPManager
from agentpool.mcp_server.shared import SharedMCPConnections
from agentpool.resource_providers.mcp_provider import MCPResourceProvider
from agentpool_config.mcp_server import StdioMCPServerConfig


psutil = pytest.importorskip("psutil")

SERVERS = Path(__file__).parent.parent / "mcp_server"
ECHO = SERVERS / "echo_server.py"
SILENT = SERVERS / "silent_server.py"
ELICITING = SERVERS / "server.py"
TIMEOUT = 60


def _config(script: Path = ECHO, name: str = "echo", **kwargs: Any) -> StdioMCPServerConfig:
    return StdioMCPServerConfig(name=name, command=sys.executable, args=[str(script)], **kwargs)


def _running(script: Path) -> list[Any]:
    """Live child processes running ``script``."""
    found = []
    for proc in psutil.Process().children(recursive=True):
        try:
            if proc.status() != psutil.STATUS_ZOMBIE and str(script) in proc.cmdline():
                found.append(proc)
        except psutil.Error:
            continue
    return found


def _servers(script: Path) -> int:
    """Server processes running ``script``.

    A launcher and the interpreter it starts (a Windows venv's python.exe) count once.
    """
    found = _running(script)
    pids = {proc.pid for proc in found}
    return sum(1 for proc in found if proc.ppid() not in pids)


async def _settled(script: Path, expected: int) -> int:
    """Number of processes running ``script``, once it reached ``expected`` (or 10 s)."""
    for _ in range(100):
        if _servers(script) == expected:
            break
        await asyncio.sleep(0.1)
    return _servers(script)


@pytest.fixture(autouse=True)
def _kill_leftovers() -> Any:
    yield
    for script in (ECHO, SILENT, ELICITING):
        for proc in _running(script):
            proc.kill()


async def test_toggling_and_exit_leave_no_server_process() -> None:
    """N3: disable/enable, reconnect and exit stop the stdio server they replace."""
    async with asyncio.timeout(TIMEOUT):
        provider = MCPResourceProvider(server=_config(), name="echo")
        async with provider:
            for _ in range(2):
                await provider.set_enabled(False)
                await provider.set_enabled(True)
            await provider.reconnect()
            assert await _settled(ECHO, 1) == 1
        assert await _settled(ECHO, 0) == 0


async def test_a_failing_server_does_not_fail_the_manager() -> None:
    """One broken server is marked failed; the others are served."""
    broken = StdioMCPServerConfig(name="broken", command="/nonexistent/mcp-server")
    async with asyncio.timeout(TIMEOUT):
        async with MCPManager(servers=[_config(), broken]) as manager:
            status = {p.server.name: p.get_status() for p in manager.get_mcp_providers()}
            assert status["echo"].status == "connected"
            assert status["broken"].status == "failed"
            assert status["broken"].error
            tools = await manager.get_aggregating_provider().get_tools()
            assert [tool.name for tool in tools] == ["echo"]
        assert await _settled(ECHO, 0) == 0


async def test_a_server_that_never_answers_times_out() -> None:
    """No hang: a server silent past its init_timeout fails, and its process is stopped."""
    silent = _config(SILENT, name="silent", init_timeout=1.0)
    async with asyncio.timeout(TIMEOUT):
        async with MCPManager(servers=[silent]) as manager:
            [provider] = manager.get_mcp_providers()
            assert provider.get_status().status == "failed"
            assert "did not start within" in (provider.get_status().error or "")
        assert await _settled(SILENT, 0) == 0


async def test_a_start_with_a_failure_leaves_no_late_server_process() -> None:
    """Startup 3d: no server finishes connecting after the start ended (and is left running).

    A broken server fails at once while another is still connecting; the start must not
    end (and close its exit stack) with that connect still running.
    """
    broken = StdioMCPServerConfig(name="broken", command="/nonexistent/mcp-server")
    manager = MCPManager(servers=[broken, _config()])
    async with asyncio.timeout(TIMEOUT):
        with contextlib.suppress(Exception):
            async with manager:
                pass
        await asyncio.sleep(3)  # Time for a connect left running to finish
        assert await _settled(ECHO, 0) == 0


async def test_cancelled_start_leaves_no_server_process() -> None:
    """A start cancelled mid-connect closes what it started (structured cancellation)."""
    manager = MCPManager(servers=[_config(), _config(SILENT, name="silent")])
    async with asyncio.timeout(TIMEOUT):
        task = asyncio.create_task(manager.__aenter__())
        await _settled(ECHO, 1)  # Started, maybe connected; the silent one never answers
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await _settled(ECHO, 0) == 0
        assert await _settled(SILENT, 0) == 0


async def test_aggregating_provider_forwards_signals_of_added_servers() -> None:
    """#49.2: change signals of a server set up later reach the aggregating provider."""
    events: list[object] = []

    async def on_change(event: object) -> None:
        events.append(event)

    async with asyncio.timeout(TIMEOUT), MCPManager() as manager:
        manager.get_aggregating_provider().tools_changed.connect(on_change)
        provider = await manager.setup_server(_config())
        assert provider is not None
        await provider.set_enabled(False)
        assert events


async def test_shared_connections_are_refcounted() -> None:
    """Sessions asking for the same server share one process; the last release stops it."""
    connections = SharedMCPConnections()
    async with asyncio.timeout(TIMEOUT):
        first, second = await asyncio.gather(
            connections.acquire(_config()), connections.acquire(_config())
        )
        assert first is second
        assert await _settled(ECHO, 1) == 1
        await connections.release(first)
        assert _servers(ECHO) == 1
        await connections.release(second)
        assert await _settled(ECHO, 0) == 0
        assert not len(connections)


def _agent_ctx(answer: bool, delay: float) -> Mock:
    async def handle_elicitation(_params: object) -> ElicitResult:
        await asyncio.sleep(delay)
        return ElicitResult(action="accept", content={"value": answer})

    ctx = Mock()
    ctx.tool_call_id = None
    ctx.report_progress = AsyncMock()
    ctx.handle_elicitation = handle_elicitation
    return ctx


async def test_concurrent_elicitations_reach_their_own_tool_call() -> None:
    """#49.3: each tool call's elicitation is answered by that call's context."""
    config = _config(ELICITING, name="eliciting")
    async with asyncio.timeout(TIMEOUT), MCPClient(config=config) as client:
        slow_yes = client.call_tool(
            "test_elicitation", Mock(), {"message": "a?"}, agent_ctx=_agent_ctx(True, 0.5)
        )
        fast_no = client.call_tool(
            "test_elicitation", Mock(), {"message": "b?"}, agent_ctx=_agent_ctx(False, 0.0)
        )
        results = await asyncio.gather(slow_yes, fast_no)
    assert [result.return_value for result in results] == ["accepted: True", "accepted: False"]
