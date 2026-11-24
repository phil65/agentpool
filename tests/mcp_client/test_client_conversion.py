"""Integration test for MCP client conversion functionality.

Tests that our MCP client properly converts FastMCP server responses
to PydanticAI-compatible return types without mocks.
"""

from __future__ import annotations

from pathlib import Path

import anyio
from pydantic_ai import BinaryContent, RunContext, RunUsage, ToolReturn
from pydantic_ai.models.test import TestModel
import pytest

from agentpool.mcp_server import MCPClient
from agentpool_config.mcp_server import StdioMCPServerConfig


@pytest.fixture
async def mcp_client():
    """Create MCP client connected to test server."""
    server_path = Path(__file__).parent / ".." / "mcp_server" / "server.py"

    config = StdioMCPServerConfig(
        name="test_server",
        command="uv",
        args=["run", str(server_path)],
    )

    client = MCPClient(config)
    async with client:
        # Wait for server to be ready
        await anyio.sleep(0.5)
        yield client


async def test_rich_content_image(mcp_client: MCPClient):
    """Test that FastMCP Image content is converted to PydanticAI types."""
    ctx = RunContext(
        tool_call_id="test-call-123",
        deps=None,
        model=TestModel(),
        usage=RunUsage(),
    )
    result = await mcp_client.call_tool(
        "test_rich_content",
        run_context=ctx,
        arguments={"content_type": "image"},
    )
    assert isinstance(result, ToolReturn)
    assert result.content
    assert isinstance(result.content[0], BinaryContent)
    result = await mcp_client.call_tool(
        "test_rich_content",
        run_context=ctx,
        arguments={"content_type": "audio"},
    )
    assert isinstance(result, ToolReturn)
    assert result.content
    assert isinstance(result.content[0], BinaryContent)
    assert result.content[0].media_type == "audio/wav"
    result = await mcp_client.call_tool(
        "test_rich_content",
        run_context=ctx,
        arguments={"content_type": "file"},
    )
    assert result is not None
    result = await mcp_client.call_tool(
        "test_rich_content",
        run_context=ctx,
        arguments={"content_type": "mixed"},
    )
    assert result is not None


if __name__ == "__main__":
    pytest.main(["-v", "-s", __file__])
