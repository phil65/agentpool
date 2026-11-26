"""Proper integration tests for ACP functionality."""

import tempfile
from unittest.mock import AsyncMock

from aclientp import ClientCapabilities
import pytest

# Add another agent to the pool for switching
from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool_server.acp_server import ACPServer
from agentpool_server.acp_server.session import ACPSession


@pytest.fixture
async def agent_pool():
    """Create a real agent pool from config."""
    pool = AgentPool()

    # Create a simple test agent with pool reference
    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    pool.register("test_agent", agent)
    return pool


async def test_acp_server_creation(agent_pool: AgentPool):
    """Test that ACP server can be created from agent pool."""
    server = ACPServer(pool=agent_pool)
    assert server.pool is agent_pool
    assert len(server.pool.get_agents()) > 0


async def test_agent_switching_workflow(agent_pool: AgentPool, mock_acp_agent):
    """Test the complete agent switching workflow."""
    multi_pool = AgentPool()

    def callback1(message: str) -> str:
        return f"Agent1 response: {message}"

    def callback2(message: str) -> str:
        return f"Agent2 response: {message}"

    agent1 = Agent.from_callback(name="agent1", callback=callback1, agent_pool=multi_pool)
    agent2 = Agent.from_callback(name="agent2", callback=callback2, agent_pool=multi_pool)

    multi_pool.register("agent1", agent1)
    multi_pool.register("agent2", agent2)
    mock_client = AsyncMock()
    capabilities = ClientCapabilities(fs=None, terminal=False)

    session = ACPSession(
        session_id="switching-test",
        agent=agent1,
        cwd=tempfile.gettempdir(),
        client=mock_client,
        acp_agent=mock_acp_agent,
        client_capabilities=capabilities,
    )

    # Should start with agent1
    assert session.agent.name == "agent1"

    # Switch to agent2
    await session.switch_active_agent("agent2")
    assert session.agent.name == "agent2"

    # Switching to non-existent agent should fail
    with pytest.raises(ValueError, match="Agent 'nonexistent' not found"):
        await session.switch_active_agent("nonexistent")


if __name__ == "__main__":
    pytest.main(["-v", __file__])
