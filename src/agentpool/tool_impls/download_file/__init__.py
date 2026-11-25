"""Download file tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agentpool.tool_impls.download_file.tool import DownloadFileTool
from agentpool_config.tools import ToolHints


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment
    from fsspec.asyn import AsyncFileSystem

__all__ = ["DownloadFileTool", "create_download_file_tool"]

# Tool metadata defaults
NAME = "download_file"
DESCRIPTION = """Download a file from a URL to the filesystem.

Supports:
- HTTP/HTTPS downloads
- Progress tracking
- Speed monitoring
- Automatic directory creation
- Configurable chunk size and timeout"""
CATEGORY: Literal["read"] = "read"
HINTS = ToolHints(open_world=True)


def create_download_file_tool(
    *,
    env: ExecutionEnvironment | None = None,
    fs: AsyncFileSystem | None = None,
    cwd: str | None = None,
    chunk_size: int = 8192,
    timeout: float = 30.0,
    name: str = NAME,
    description: str = DESCRIPTION,
    requires_confirmation: bool = False,
) -> DownloadFileTool:
    """Create a configured DownloadFileTool instance.

    Args:
        env: Execution environment to use. Falls back to agent.env if not set.
        fs: Filesystem to operate on instead of the environment's (custom filesystems).
        cwd: Working directory for resolving relative paths.
        chunk_size: Size of chunks to download in bytes (default: 8192).
        timeout: Request timeout in seconds (default: 30.0).
        name: Tool name override.
        description: Tool description override.
        requires_confirmation: Whether tool execution needs confirmation.

    Returns:
        Configured DownloadFileTool instance.

    Example:
        # Basic usage
        download = create_download_file_tool()

        # With custom settings
        download = create_download_file_tool(
            chunk_size=16384,
            timeout=60.0,
        )

        # With specific environment
        download = create_download_file_tool(
            env=my_env,
            cwd="/workspace/downloads",
        )
    """
    return DownloadFileTool(
        name=name,
        description=description,
        category=CATEGORY,
        hints=HINTS,
        env=env,
        fs=fs,
        cwd=cwd,
        chunk_size=chunk_size,
        timeout=timeout,
        requires_confirmation=requires_confirmation,
    )
