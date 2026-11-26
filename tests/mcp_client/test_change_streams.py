"""MCPClient learns about a server's list changes on either protocol era."""

import asyncio
from pathlib import Path
import sys

from agentpool.mcp_server.client import MCPClient
from agentpool_config.mcp_server import StdioMCPServerConfig


SERVER = Path(__file__).parent.parent / "mcp_server" / "changing_server.py"


async def test_tool_list_changes_reach_the_callback() -> None:
    changed = asyncio.Event()

    async def on_tools_changed(meta: object) -> None:
        changed.set()

    config = StdioMCPServerConfig(name="changing", command=sys.executable, args=[str(SERVER)])
    async with MCPClient(config, tool_change_callback=on_tools_changed) as client:
        assert {tool.name for tool in await client.list_tools()} == {"grow"}
        await asyncio.sleep(0.2)  # Let the change stream be acknowledged
        await client.fastmcp_client.call_tool("grow", {})
        await asyncio.wait_for(changed.wait(), timeout=10)
        assert {tool.name for tool in await client.list_tools()} == {"grow", "extra"}
