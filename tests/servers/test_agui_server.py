"""Unit tests for AGUIServer."""

import pytest

from agentpool import Agent, AgentPool
from agentpool_server.agui_server import AGUIServer


# Test constants
TEST_PORT_BASE = 8002
AGENT_COUNT = 2
EXPECTED_ROUTES = 3  # 2 agents + 1 root endpoint


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


async def test_agui_server_creation(simple_agent_pool: AgentPool):
    """Test AGUIServer can be created with agent pool."""
    server = AGUIServer(simple_agent_pool, host="localhost", port=TEST_PORT_BASE)
    assert server.pool is simple_agent_pool
    assert server.host == "localhost"
    assert server.port == TEST_PORT_BASE
    assert len(server.pool.get_agents()) == AGENT_COUNT


async def test_agui_server_initialization(simple_agent_pool: AgentPool):
    """Test AGUIServer initialization with pool context."""
    server = AGUIServer(simple_agent_pool, host="localhost", port=TEST_PORT_BASE + 1)

    async with server:
        # Pool should be initialized
        assert server.pool is not None
        assert len(server.pool.get_agents()) == AGENT_COUNT


async def test_agui_server_base_url(simple_agent_pool: AgentPool):
    """Test AGUIServer URL generation."""
    port = TEST_PORT_BASE + 2
    server = AGUIServer(simple_agent_pool, host="localhost", port=port)
    assert server.base_url == f"http://localhost:{port}"


async def test_agui_server_agent_url(simple_agent_pool: AgentPool):
    """Test AGUIServer agent URL generation."""
    port = TEST_PORT_BASE + 3
    server = AGUIServer(simple_agent_pool, host="localhost", port=port)
    url = server.get_agent_url("agent1")
    assert url == f"http://localhost:{port}/agent1"


async def test_agui_server_list_routes(simple_agent_pool: AgentPool):
    """Test AGUIServer route listing."""
    port = TEST_PORT_BASE + 4
    server = AGUIServer(simple_agent_pool, host="localhost", port=port)
    routes = server.list_agent_routes()

    assert isinstance(routes, dict)
    assert len(routes) == AGENT_COUNT
    assert "agent1" in routes
    assert "agent2" in routes
    assert routes["agent1"] == f"http://localhost:{port}/agent1"
    assert routes["agent2"] == f"http://localhost:{port}/agent2"


async def test_agui_server_app_creation(simple_agent_pool: AgentPool):
    """Test AGUIServer Starlette app creation."""
    pytest.importorskip("starlette")

    server = AGUIServer(simple_agent_pool, host="localhost", port=TEST_PORT_BASE + 5)

    async with server:
        routes = await server.get_routes()
        assert routes is not None
        # Should have routes for each agent plus root
        assert len(routes) == EXPECTED_ROUTES


@pytest.mark.skip(reason="Requires specific YAML schema - integration test")
async def test_agui_server_from_config(tmp_path):
    """Test AGUIServer creation from config file."""
    pytest.importorskip("starlette")

    # Create a minimal config file
    config_content = """
    environments:
      test:
        type: inline
        module_path: agentpool
        class_name: Agent

    agents:
      test_agent:
        type: native
        name: test_agent
        environment: test
        model: test
        system_prompt: You are a test agent
    """

    config_path = tmp_path / "test_config.yml"
    config_path.write_text(config_content)

    port = TEST_PORT_BASE + 6
    server = AGUIServer.from_config(config_path, host="localhost", port=port)
    assert server.host == "localhost"
    assert server.port == port
    assert len(server.pool.get_agents()) >= 1


async def test_agui_server_name_generation(simple_agent_pool: AgentPool):
    """Test AGUIServer auto-generates name when not provided."""
    server = AGUIServer(simple_agent_pool)
    assert server.name is not None
    assert "AGUIServer" in server.name


async def test_agui_server_custom_name(simple_agent_pool: AgentPool):
    """Test AGUIServer uses custom name when provided."""
    server = AGUIServer(simple_agent_pool, name="custom-agui-server")
    assert server.name == "custom-agui-server"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
