"""Grep search tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agentpool.tool_impls.grep.tool import GrepTool
from agentpool_config.tools import ToolHints


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment
    from fsspec.asyn import AsyncFileSystem

__all__ = ["GrepTool", "create_grep_tool"]

# Tool metadata defaults
NAME = "grep"
DESCRIPTION = """Search file contents for patterns using grep.

Supports:
- Regex pattern matching
- File filtering with glob patterns
- Context lines before/after matches
- Fast subprocess grep (ripgrep/grep) with Python fallback
- Case-sensitive and case-insensitive search"""
CATEGORY: Literal["search"] = "search"
HINTS = ToolHints(read_only=True, idempotent=True)


def create_grep_tool(
    *,
    env: ExecutionEnvironment | None = None,
    fs: AsyncFileSystem | None = None,
    cwd: str | None = None,
    max_output_kb: int = 64,
    use_subprocess_grep: bool = True,
    name: str = NAME,
    description: str = DESCRIPTION,
    requires_confirmation: bool = False,
) -> GrepTool:
    """Create a configured GrepTool instance.

    Args:
        env: Execution environment to use. Falls back to agent.env if not set.
        fs: Filesystem to operate on instead of the environment's (custom filesystems).
        cwd: Working directory for resolving relative paths.
        max_output_kb: Maximum output size in KB (default: 64KB).
        use_subprocess_grep: Use ripgrep/grep subprocess if available (default: True).
        name: Tool name override.
        description: Tool description override.
        requires_confirmation: Whether tool execution needs confirmation.

    Returns:
        Configured GrepTool instance.

    Example:
        # Basic usage
        grep = create_grep_tool()

        # With custom limits
        grep = create_grep_tool(
            max_output_kb=128,
            use_subprocess_grep=False,  # Force Python implementation
        )

        # With specific environment
        grep = create_grep_tool(
            env=my_env,
            cwd="/workspace",
        )
    """
    return GrepTool(
        name=name,
        description=description,
        category=CATEGORY,
        hints=HINTS,
        env=env,
        fs=fs,
        cwd=cwd,
        max_output_kb=max_output_kb,
        use_subprocess_grep=use_subprocess_grep,
        requires_confirmation=requires_confirmation,
    )
