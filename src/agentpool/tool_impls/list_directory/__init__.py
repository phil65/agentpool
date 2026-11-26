"""List directory tool."""

from typing import TYPE_CHECKING, Literal

from agentpool.tool_impls.list_directory.tool import ListDirectoryTool
from agentpool_config.tools import ToolHints


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment
    from fsspec.asyn import AsyncFileSystem

__all__ = ["ListDirectoryTool", "create_list_directory_tool"]

# Tool metadata defaults
NAME = "list_directory"
DESCRIPTION = """List files in a directory with filtering support.

Supports:
- Glob pattern matching (*, **, *.py, etc.)
- Exclude patterns for filtering
- Recursive directory traversal with depth control
- Safety limits to prevent overwhelming output"""
CATEGORY: Literal["read"] = "read"
HINTS = ToolHints(read_only=True, idempotent=True)


def create_list_directory_tool(
    *,
    env: ExecutionEnvironment | None = None,
    fs: AsyncFileSystem | None = None,
    cwd: str | None = None,
    max_items: int = 500,
    name: str = NAME,
    description: str = DESCRIPTION,
    requires_confirmation: bool = False,
) -> ListDirectoryTool:
    """Create a configured ListDirectoryTool instance.

    Args:
        env: Execution environment to use. Falls back to agent.env if not set.
        fs: Filesystem to operate on instead of the environment's (custom filesystems).
        cwd: Working directory for resolving relative paths.
        max_items: Maximum number of items to return (safety limit, default: 500).
        name: Tool name override.
        description: Tool description override.
        requires_confirmation: Whether tool execution needs confirmation.

    Returns:
        Configured ListDirectoryTool instance.

    Example:
        # Basic usage
        ls = create_list_directory_tool()

        # With custom limits
        ls = create_list_directory_tool(max_items=1000)

        # With specific environment
        ls = create_list_directory_tool(
            env=my_env,
            cwd="/workspace",
        )
    """
    return ListDirectoryTool(
        name=name,
        description=description,
        category=CATEGORY,
        hints=HINTS,
        env=env,
        fs=fs,
        cwd=cwd,
        max_items=max_items,
        requires_confirmation=requires_confirmation,
    )
