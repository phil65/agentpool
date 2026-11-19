"""Test fixtures for ACP tests."""

from __future__ import annotations

from unittest.mock import Mock

from aclientp import ClientCapabilities, DefaultACPClient, FileSystemCapabilities
from aclientp.agent.implementations import TestAgent
import pytest

from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


@pytest.fixture
def test_client() -> DefaultACPClient:
    """Create a fresh test client for each test."""
    return DefaultACPClient(allow_file_operations=True, use_real_files=False)


@pytest.fixture
def test_agent() -> TestAgent:
    """Create a fresh test agent for each test."""
    return TestAgent()


@pytest.fixture
def mock_connection():
    """Create a mock ACP connection."""
    return Mock()


@pytest.fixture
def mock_agent_pool_with_agent() -> tuple[AgentPool, Agent]:
    """Create a mock agent pool with a test agent, returning both."""

    # Create a simple test agent
    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    pool = AgentPool()
    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    pool.register("test_agent", agent)
    return pool, agent


@pytest.fixture
def mock_agent_pool(mock_agent_pool_with_agent: tuple[AgentPool, Agent]) -> AgentPool:
    """Create a mock agent pool with a test agent."""
    return mock_agent_pool_with_agent[0]


@pytest.fixture
def default_test_agent(mock_agent_pool_with_agent: tuple[AgentPool, Agent]) -> Agent:
    """Get the default test agent from the mock pool."""
    return mock_agent_pool_with_agent[1]


@pytest.fixture
def client_capabilities():
    """Create client capabilities for testing."""
    fs_caps = FileSystemCapabilities(read_text_file=True, write_text_file=True)
    return ClientCapabilities(fs=fs_caps, terminal=True)


@pytest.fixture
def mock_acp_agent(
    mock_connection, default_test_agent: Agent, client_capabilities
) -> AgentPoolACPAgent:
    """Create a mock ACP agent for testing."""
    return AgentPoolACPAgent(client=mock_connection, default_agent=default_test_agent)


@pytest.fixture
def acp_agent(mock_connection, default_test_agent: Agent, client_capabilities) -> AgentPoolACPAgent:
    """Alias for mock_acp_agent - used by some tests."""
    return AgentPoolACPAgent(client=mock_connection, default_agent=default_test_agent)
