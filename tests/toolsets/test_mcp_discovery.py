"""Tests for MCP Discovery Toolset.

Tests that the toolset properly wires up elicitation and progress
reporting through the MCPClient wrapper.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.types import ElicitResult
from pydantic_ai import RunContext
import pytest

from agentpool.agents.context import AgentContext
from agentpool.mcp_server.client import MCPClient
from agentpool_config.mcp_server import StdioMCPServerConfig
from agentpool_toolsets.mcp_discovery.toolset import MCPDiscoveryToolset


pytestmark = pytest.mark.asyncio

# Test server config for integration tests
TEST_SERVER_CONFIG = StdioMCPServerConfig(
    command="uv",
    args=["run", "tests/mcp_server/server.py"],
)


# Unit tests for toolset configuration


async def test_toolset_initialization():
    """Test that toolset initializes and exposes its tools."""
    toolset = MCPDiscoveryToolset(name="test_discovery")

    # Verify tools can be loaded
    tools = await toolset.get_tools()
    assert len(tools) == 3
    assert any(t.name == "search_mcp_servers" for t in tools)
    assert any(t.name == "list_mcp_tools" for t in tools)
    assert any(t.name == "call_mcp_tool" for t in tools)


async def test_connection_is_created_lazily():
    """Test that MCPClient connections are created from the server config."""
    toolset = MCPDiscoveryToolset(name="test_discovery")

    # Mock the registry and server config to avoid real connection
    mock_config = MagicMock()
    mock_client = AsyncMock()
    mock_client.connected = True

    with (
        patch.object(toolset, "_get_server_config", return_value=mock_config),
        patch("agentpool_toolsets.mcp_discovery.toolset.MCPClient") as mock_client_cls,
    ):
        mock_client_cls.return_value = mock_client

        # Trigger connection
        await toolset._get_connection("test-server")

        mock_client_cls.assert_called_once_with(config=mock_config)


# Integration tests using local test MCP server


async def test_mcp_client_call_tool_with_progress():
    """Test that MCPClient.call_tool properly handles progress reporting."""
    client = MCPClient(config=TEST_SERVER_CONFIG)
    run_ctx = AsyncMock(spec=RunContext)
    agent_ctx = AsyncMock(spec=AgentContext)
    agent_ctx.report_progress = AsyncMock()

    async with client:
        result = await client.call_tool(
            name="test_progress",
            run_context=run_ctx,
            arguments={"message": "test"},
            agent_ctx=agent_ctx,
        )

        # Verify progress was reported 3 times (0%, 50%, 99%)
        assert agent_ctx.report_progress.called
        assert agent_ctx.report_progress.call_count == 3
        assert "test" in str(result)


async def test_mcp_client_call_tool_with_elicitation():
    """Test that MCPClient.call_tool properly handles elicitation."""
    client = MCPClient(config=TEST_SERVER_CONFIG)
    run_ctx = AsyncMock(spec=RunContext)
    agent_ctx = AsyncMock(spec=AgentContext)
    agent_ctx.handle_elicitation = AsyncMock(
        return_value=ElicitResult(action="accept", content={"value": True})
    )

    async with client:
        await client.call_tool(
            name="test_elicitation",
            run_context=run_ctx,
            arguments={"message": "test elicit"},
            agent_ctx=agent_ctx,
        )

        # Verify elicitation was called
        assert agent_ctx.handle_elicitation.called
        assert "test elicit" in str(agent_ctx.handle_elicitation.call_args)


async def test_converted_tool_has_both_contexts():
    """Test that tools converted from MCP schema include both RunContext and AgentContext."""
    client = MCPClient(config=TEST_SERVER_CONFIG)

    async with client:
        mcp_tools = await client.list_tools()
        test_tool = next(t for t in mcp_tools if t.name == "test_progress")
        tool = client.convert_tool(test_tool)

        # Check signature includes both contexts
        sig = inspect.signature(tool.fn)
        params = list(sig.parameters.keys())
        assert "ctx" in params  # RunContext
        assert "agent_ctx" in params  # AgentContext
        assert "message" in params  # Original parameter
