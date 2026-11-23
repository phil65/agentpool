"""FastMCP test server whose `grow` tool adds a tool later, outside any request.

Like a server that reloads its tools in the background: the change is announced with
`notify_tools_changed()`, which reaches clients on a `subscriptions/listen` stream only.
"""

import asyncio

from fastmcp import FastMCP


mcp = FastMCP("Changing Server")
_background: set[asyncio.Task[None]] = set()


async def _add_extra() -> None:
    await asyncio.sleep(0.1)  # After the call that asked for it has returned

    @mcp.tool
    def extra() -> str:
        return "extra"

    await mcp.notify_tools_changed()


@mcp.tool
async def grow() -> str:
    """Add the `extra` tool shortly, in the background."""
    task = asyncio.create_task(_add_extra())
    _background.add(task)
    task.add_done_callback(_background.discard)
    return "growing"


if __name__ == "__main__":
    mcp.run(show_banner=False, log_level="error")
