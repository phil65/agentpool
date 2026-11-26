"""MCPClient calls tools that only run as background tasks (SEP-2663)."""

import importlib.util
from pathlib import Path
import sys

import pytest

from agentpool.mcp_server.client import MCPClient
from agentpool_config.mcp_server import StdioMCPServerConfig


# Skip without importing it: importing fastmcp_tasks registers its client half
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("fastmcp_tasks") is None, reason="needs the mcp-tasks extra"
)

SERVER = Path(__file__).parent.parent / "mcp_server" / "tasks_server.py"


async def test_task_only_tool_is_called_and_awaited() -> None:
    config = StdioMCPServerConfig(name="tasks", command=sys.executable, args=[str(SERVER)])
    async with MCPClient(config) as client:
        result = await client.fastmcp_client.call_tool("slow_double", {"x": 21})
    assert result.data == 42
