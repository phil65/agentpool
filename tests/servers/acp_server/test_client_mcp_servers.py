"""serve-acp with client MCP servers and failing pool parts (startup robustness).

The client side is aclientp's, talking to a real ``ACPServer`` over a local WebSocket.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import socket
import sys
from typing import TYPE_CHECKING, Any, Self

from aclientp import RequestError, StdioMcpServer
from aclientp.client.connection import ClientSideConnection
from aclientp.client.implementations import HeadlessACPClient, NoOpClient
from aclientp.schema import (
    AgentMessageChunk,
    CloseSessionRequest,
    InitializeRequest,
    NewSessionRequest,
    PromptRequest,
    TextContentBlock,
)
from aclientp.ws_transport import WebSocketTransportError, connect_remote_transport
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool, AgentsManifest
from agentpool.sessions.environment import use_session_environment
from agentpool_cli.serve_acp import _network_transport
from agentpool_config.mcp_server import StdioMCPServerConfig
from agentpool_server.acp_server import ACPServer
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from aclientp.schema import SessionNotification


psutil = pytest.importorskip("psutil")

ECHO = Path(__file__).parent.parent.parent / "mcp_server" / "echo_server.py"
TIMEOUT = 60


def _echo_server() -> StdioMcpServer:
    return StdioMcpServer(name="echo", command=sys.executable, args=[str(ECHO)], env=[])


def _echo_processes() -> int:
    count = 0
    for proc in psutil.Process().children(recursive=True):
        try:
            if proc.status() != psutil.STATUS_ZOMBIE and str(ECHO) in proc.cmdline():
                count += 1
        except psutil.Error:
            continue
    return count


async def _settled(expected: int) -> int:
    for _ in range(100):
        if _echo_processes() == expected:
            break
        await asyncio.sleep(0.1)
    return _echo_processes()


@pytest.fixture(autouse=True)
def _kill_leftovers() -> Any:
    yield
    for proc in psutil.Process().children(recursive=True):
        try:
            if str(ECHO) in proc.cmdline():
                proc.kill()
        except psutil.Error:
            continue


class _RecordingClient(NoOpClient):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    async def session_update(self, params: SessionNotification) -> None:
        match params.update:
            case AgentMessageChunk(content=TextContentBlock(text=text)):
                self.texts.append(text)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


class _Served:
    """A pool served by serve-acp on a local WebSocket, and a connected client."""

    def __init__(self, pool: AgentPool) -> None:
        self.pool = pool
        self.client = _RecordingClient()
        port = _free_port()
        self.url = f"ws://127.0.0.1:{port}"
        transport = _network_transport("websocket", "127.0.0.1", port, None, [])
        self.server = ACPServer(pool, transport=transport, load_skills=False)

    async def __aenter__(self) -> Self:
        await self.server.__aenter__()
        self._task = asyncio.create_task(self.server.start())
        self._remote = None
        for _ in range(400):
            try:
                self._remote = connect_remote_transport(self.url)
                reader, writer = await self._remote.__aenter__()
            except WebSocketTransportError, OSError:
                await asyncio.sleep(0.025)
            else:
                break
        else:
            pytest.fail("serve-acp did not start")
        self.conn = ClientSideConnection(lambda _agent: self.client, writer, reader)
        await self.conn.initialize(InitializeRequest(protocol_version=1))
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.conn.close()
        assert self._remote is not None
        await self._remote.__aexit__(None, None, None)
        self.server._shutdown_event.set()
        await asyncio.wait_for(self._task, TIMEOUT)
        await self.server.__aexit__(None, None, None)

    async def new_session(self, *, mcp: bool) -> str:
        servers = [_echo_server()] if mcp else []
        response = await self.conn.new_session(NewSessionRequest(cwd=".", mcp_servers=servers))
        return response.session_id

    async def prompt(self, session_id: str) -> str:
        request = PromptRequest(session_id=session_id, prompt=[TextContentBlock(text="hi")])
        response = await self.conn.prompt(request)
        return response.stop_reason


def _pool(*, echo_tool: bool = False) -> AgentPool:
    pool = AgentPool()
    agent = Agent(name="main", model=TestModel(), agent_pool=pool)
    if echo_tool:

        def echo(text: str) -> str:
            """Clashes with the client server's tool."""
            return text

        agent.tools.register_tool(echo)
    pool.register("main", agent)
    return pool


@pytest.fixture
async def served() -> AsyncIterator[_Served]:
    async with asyncio.timeout(TIMEOUT), _Served(_pool()) as served:
        yield served


async def test_client_mcp_servers_belong_to_their_sessions(served: _Served) -> None:
    """Startup 3a: sessions share the client's server; closing them stops it; nothing bricks."""
    async with asyncio.timeout(TIMEOUT):
        first = await served.new_session(mcp=True)
        second = await served.new_session(mcp=True)
        assert await _settled(1) == 1  # One shared connection, not one per session
        assert await served.prompt(second) == "end_turn"
        await served.conn.close_session(CloseSessionRequest(session_id=first))
        await served.conn.close_session(CloseSessionRequest(session_id=second))
        assert await _settled(0) == 0
        third = await served.new_session(mcp=False)
        assert await served.prompt(third) == "end_turn"
    assert not [text for text in served.client.texts if "error" in text.lower()]
    agent = served.pool.all_agents["main"]
    assert not agent.mcp.get_mcp_providers()  # The shared agent got none of them


async def test_client_mcp_tools_are_only_in_their_session() -> None:
    pool = _pool()
    agent = pool.all_agents["main"]
    async with asyncio.timeout(TIMEOUT), pool:
        acp_agent = AgentPoolACPAgent(client=HeadlessACPClient(), default_agent=agent)
        await acp_agent.initialize(InitializeRequest(protocol_version=1))
        request = NewSessionRequest(cwd=".", mcp_servers=[_echo_server()])
        with_mcp = (await acp_agent.new_session(request)).session_id
        without = (await acp_agent.new_session(NewSessionRequest(cwd="."))).session_id
        manager = acp_agent.session_manager

        async def tool_names(session_id: str) -> set[str]:
            session = manager.get_session(session_id)
            assert session is not None
            with use_session_environment(session.environment):
                return {tool.name for tool in await agent.tools.get_tools()}

        assert "echo" in await tool_names(with_mcp)
        assert "echo" not in await tool_names(without)
        assert "echo" not in {tool.name for tool in await agent.tools.get_tools()}
        await manager.__aexit__(None, None, None)
        assert await _settled(0) == 0


async def test_clashing_tool_names_fail_the_prompt() -> None:
    """A turn that can't be set up (tool names clash) is an error, not a quiet end_turn."""
    async with asyncio.timeout(TIMEOUT), _Served(_pool(echo_tool=True)) as served:
        session_id = await served.new_session(mcp=True)
        with pytest.raises(RequestError) as raised:
            await served.prompt(session_id)
        assert "conflicts" in str(raised.value.data)


async def test_pool_mcp_prompts_become_commands_without_client_servers() -> None:
    """Startup 3f: pool MCP prompts are slash commands even when the client sends none."""
    echo = StdioMCPServerConfig(name="echo", command=sys.executable, args=[str(ECHO)])
    pool = AgentPool(AgentsManifest(mcp_servers=[echo]))
    agent = Agent(name="main", model=TestModel(), agent_pool=pool)
    pool.register("main", agent)
    async with asyncio.timeout(TIMEOUT), pool:
        acp_agent = AgentPoolACPAgent(client=HeadlessACPClient(), default_agent=agent)
        await acp_agent.initialize(InitializeRequest(protocol_version=1))
        response = await acp_agent.new_session(NewSessionRequest(cwd="."))
        await acp_agent.tasks.complete_tasks()
        session = acp_agent.session_manager.get_session(response.session_id)
        assert session is not None
        assert "greet" in {command.name for command in session.list_commands()}
        await acp_agent.session_manager.close_session(response.session_id)


class _BrokenAgent(Agent[None, str]):
    async def __aenter__(self) -> Self:
        raise RuntimeError("backend missing")


async def test_a_failing_agent_does_not_kill_the_pool() -> None:
    """Startup 3b: the pool starts without it; using it raises a clear error."""
    pool = AgentPool()
    good = Agent(name="good", model=TestModel(), agent_pool=pool)
    broken = _BrokenAgent(name="broken", model=TestModel(), agent_pool=pool)
    pool.register("good", good)
    pool.register("broken", broken)
    async with asyncio.timeout(TIMEOUT), pool:
        assert "backend missing" in pool.failed_nodes["broken"]
        assert (await good.run("hi")).content
        with pytest.raises(RuntimeError, match="'broken' failed to start"):
            pool.get_agent("broken")
        acp_agent = AgentPoolACPAgent(client=HeadlessACPClient(), default_agent=broken)
        await acp_agent.initialize(InitializeRequest(protocol_version=1))
        with pytest.raises(RequestError):
            await acp_agent.new_session(NewSessionRequest(cwd="."))


async def test_a_failing_pool_mcp_server_does_not_kill_the_pool() -> None:
    """Startup 3b: a broken pool MCP server is marked failed; the pool serves."""
    broken = StdioMCPServerConfig(name="broken", command="/nonexistent/mcp-server")
    pool = AgentPool(AgentsManifest(mcp_servers=[broken]))
    agent = Agent(name="main", model=TestModel(), agent_pool=pool)
    pool.register("main", agent)
    async with asyncio.timeout(TIMEOUT), pool:
        [provider] = pool.mcp.get_mcp_providers()
        assert provider.get_status().status == "failed"
        assert (await agent.run("hi")).content
