"""FastMCP test server with a tool that only runs as a background task (SEP-2663)."""

import asyncio

from fastmcp import FastMCP
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension


mcp = FastMCP("Tasks Server")
mcp.add_extension(TasksExtension())


@mcp.tool(task=TaskConfig(mode="required"))
async def slow_double(x: int) -> int:
    """Double x, slowly (task-only)."""
    await asyncio.sleep(0.2)
    return x * 2


if __name__ == "__main__":
    mcp.run(show_banner=False, log_level="error")
