"""Delete path tool."""

from typing import TYPE_CHECKING, Literal

from agentpool.tool_impls.delete_path.tool import DeletePathTool
from agentpool_config.tools import ToolHints


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment
    from fsspec.asyn import AsyncFileSystem

__all__ = ["DeletePathTool", "create_delete_path_tool"]

# Tool metadata defaults
NAME = "delete_path"
DESCRIPTION = """Delete a file or directory from the filesystem.

Supports:
- File deletion
- Directory deletion with safety checks
- Recursive directory deletion
- Empty directory validation"""
CATEGORY: Literal["delete"] = "delete"
HINTS = ToolHints(destructive=True)


def create_delete_path_tool(
    *,
    env: ExecutionEnvironment | None = None,
    fs: AsyncFileSystem | None = None,
    cwd: str | None = None,
    name: str = NAME,
    description: str = DESCRIPTION,
    requires_confirmation: bool = False,
) -> DeletePathTool:
    """Create a configured DeletePathTool instance.

    Args:
        env: Execution environment to use. Falls back to agent.env if not set.
        fs: Filesystem to operate on instead of the environment's (custom filesystems).
        cwd: Working directory for resolving relative paths.
        name: Tool name override.
        description: Tool description override.
        requires_confirmation: Whether tool execution needs confirmation.

    Returns:
        Configured DeletePathTool instance.

    Example:
        # Basic usage
        delete = create_delete_path_tool()

        # With confirmation required for safety
        delete = create_delete_path_tool(requires_confirmation=True)

        # With specific environment
        delete = create_delete_path_tool(
            env=my_env,
            cwd="/workspace",
        )
    """
    return DeletePathTool(
        name=name,
        description=description,
        category=CATEGORY,
        hints=HINTS,
        env=env,
        fs=fs,
        cwd=cwd,
        requires_confirmation=requires_confirmation,
    )
