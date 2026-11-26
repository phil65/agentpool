"""Unit tests for AggregatingServer."""

import pytest

from agentpool import Agent, AgentPool
from agentpool_server import A2AServer, AggregatingServer, AGUIServer


# Test constants
TEST_PORT_BASE = 9000
SERVER_COUNT = 2


@pytest.fixture
def simple_agent_pool():
    """Create a simple agent pool for testing."""

    def callback1(message: str) -> str:
        return f"Agent1: {message}"

    def callback2(message: str) -> str:
        return f"Agent2: {message}"

    agent1 = Agent.from_callback(name="agent1", callback=callback1)
    agent2 = Agent.from_callback(name="agent2", callback=callback2)

    pool = AgentPool()
    pool.register("agent1", agent1)
    pool.register("agent2", agent2)
    return pool


@pytest.fixture
def agui_server(simple_agent_pool: AgentPool):
    """Create AGUIServer for testing."""
    return AGUIServer(simple_agent_pool, host="localhost", port=TEST_PORT_BASE)


@pytest.fixture
def a2a_server(simple_agent_pool: AgentPool):
    """Create A2AServer for testing."""
    return A2AServer(simple_agent_pool, host="localhost", port=TEST_PORT_BASE + 1)


async def test_aggregating_server_creation(simple_agent_pool: AgentPool):
    """Test AggregatingServer can be created with multiple servers."""
    agui = AGUIServer(simple_agent_pool, port=TEST_PORT_BASE + 10)
    a2a = A2AServer(simple_agent_pool, port=TEST_PORT_BASE + 11)

    server = AggregatingServer(simple_agent_pool, servers=[agui, a2a])

    assert server.pool is simple_agent_pool
    assert len(server.servers) == SERVER_COUNT


async def test_aggregating_server_requires_servers(simple_agent_pool: AgentPool):
    """Test AggregatingServer requires at least one server."""
    with pytest.raises(ValueError, match="At least one server must be provided"):
        AggregatingServer(simple_agent_pool, servers=[])


async def test_aggregating_server_initialization(simple_agent_pool: AgentPool):
    """Test AggregatingServer initialization with pool context."""
    agui = AGUIServer(simple_agent_pool, port=TEST_PORT_BASE + 50)
    a2a = A2AServer(simple_agent_pool, port=TEST_PORT_BASE + 51)

    server = AggregatingServer(simple_agent_pool, servers=[agui, a2a])

    async with server:
        assert server.initialized_server_count == SERVER_COUNT
        assert len(server._initialized_servers) == SERVER_COUNT


async def test_aggregating_server_list_servers(simple_agent_pool: AgentPool):
    """Test AggregatingServer server listing."""
    agui = AGUIServer(simple_agent_pool, port=TEST_PORT_BASE + 60, name="agui-test")
    a2a = A2AServer(simple_agent_pool, port=TEST_PORT_BASE + 61, name="a2a-test")

    server = AggregatingServer(simple_agent_pool, servers=[agui, a2a])

    servers_info = server.list_servers()
    assert len(servers_info) == SERVER_COUNT

    names = [s.name for s in servers_info]
    assert "agui-test" in names
    assert "a2a-test" in names


async def test_aggregating_server_get_server(simple_agent_pool: AgentPool):
    """Test AggregatingServer get_server method."""
    agui = AGUIServer(simple_agent_pool, port=TEST_PORT_BASE + 70, name="agui-find")
    a2a = A2AServer(simple_agent_pool, port=TEST_PORT_BASE + 71, name="a2a-find")

    server = AggregatingServer(simple_agent_pool, servers=[agui, a2a])

    found = server.get_server("agui-find")
    assert found is not None
    assert found.name == "agui-find"

    not_found = server.get_server("nonexistent")
    assert not_found is None


async def test_aggregating_server_add_remove_server(simple_agent_pool: AgentPool):
    """Test adding and removing servers from AggregatingServer."""
    agui = AGUIServer(simple_agent_pool, port=TEST_PORT_BASE + 80, name="agui-add")

    server = AggregatingServer(simple_agent_pool, servers=[agui])
    initial_count = 1
    assert len(server.servers) == initial_count

    # Add server
    a2a = A2AServer(simple_agent_pool, port=TEST_PORT_BASE + 81, name="a2a-add")
    server.add_server(a2a)
    assert len(server.servers) == initial_count + 1

    # Remove server
    server.remove_server(a2a)
    assert len(server.servers) == initial_count


async def test_aggregating_server_status(simple_agent_pool: AgentPool):
    """Test server status tracking."""
    agui = AGUIServer(simple_agent_pool, port=TEST_PORT_BASE + 120, name="status-test")

    server = AggregatingServer(simple_agent_pool, servers=[agui])

    # Before initialization
    status = server.get_server_status()
    assert status["status-test"] == "not_initialized"

    async with server:
        # After initialization
        status = server.get_server_status()
        assert status["status-test"] == "initialized"


async def test_aggregating_server_repr(simple_agent_pool: AgentPool):
    """Test AggregatingServer string representation."""
    agui = AGUIServer(simple_agent_pool, port=TEST_PORT_BASE + 130)

    server = AggregatingServer(
        simple_agent_pool,
        servers=[agui],
        name="test-aggregator",
    )

    repr_str = repr(server)
    assert "AggregatingServer" in repr_str
    assert "test-aggregator" in repr_str
    assert "servers=1" in repr_str


async def test_aggregating_server_running_count(simple_agent_pool: AgentPool):
    """Test running server count tracking."""
    agui = AGUIServer(simple_agent_pool, port=TEST_PORT_BASE + 140)

    server = AggregatingServer(simple_agent_pool, servers=[agui])

    # Before initialization
    assert server.running_server_count == 0

    async with server:
        # After initialization but before starting
        assert server.initialized_server_count == 1
        # Not running yet (would need run_context())
        assert server.running_server_count == 0


if __name__ == "__main__":
    pytest.main(["-v", __file__])


class _FailingServer(AGUIServer):
    async def __aenter__(self):  # type: ignore[override]
        raise RuntimeError("cannot start")


async def test_a_failed_enter_leaves_the_pool_exited(simple_agent_pool: AgentPool):
    """#61.4b: the aggregating server's own pool entry is undone on a failed enter."""
    failing = _FailingServer(simple_agent_pool, port=TEST_PORT_BASE + 20)
    server = AggregatingServer(simple_agent_pool, servers=[failing], raise_exceptions=True)
    with pytest.raises(RuntimeError, match="cannot start"):
        await server.__aenter__()
    assert not simple_agent_pool.is_running


async def test_cancelling_waits_for_the_stopped_servers(simple_agent_pool: AgentPool):
    """#61.4b: after a cancel, the child servers' tasks have ended (not left running)."""
    import asyncio

    from agentpool_server.base import BaseServer

    class _Forever(BaseServer):
        async def _start_async(self) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)  # Takes a moment to stop
                raise

    children = [_Forever(simple_agent_pool), _Forever(simple_agent_pool)]
    async with AggregatingServer(simple_agent_pool, servers=children) as server:
        running = asyncio.create_task(server._start_async())
        await asyncio.sleep(0.05)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert all(c._server_task is not None and c._server_task.done() for c in children)
