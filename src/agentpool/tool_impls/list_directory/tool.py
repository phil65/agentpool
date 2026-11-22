"""List directory tool implementation."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from upathtools import is_directory

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.tools.base import Tool, ToolResult


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from exxec import ExecutionEnvironment
    from fsspec.asyn import AsyncFileSystem


logger = get_logger(__name__)


@dataclass
class ListDirectoryTool(Tool[ToolResult]):
    """List files in a directory with filtering support.

    A standalone tool for listing directory contents with:
    - Glob pattern matching (*, **, *.py, etc.)
    - Exclude patterns for filtering
    - Recursive directory traversal with depth control
    - Formatted markdown output

    Use create_list_directory_tool() factory for convenient instantiation.
    """

    # Tool-specific configuration
    env: ExecutionEnvironment | None = None
    """Execution environment to use. Falls back to agent.env if not set."""

    cwd: str | None = None
    """Working directory for resolving relative paths."""

    max_items: int = 500
    """Maximum number of items to return (safety limit)."""

    def get_callable(self) -> Callable[..., Awaitable[ToolResult]]:
        """Return the list_directory method as the callable."""
        return self._list_directory

    def _get_fs(self, ctx: AgentContext) -> AsyncFileSystem:
        """Get filesystem from env, falling back to agent's env if not set."""
        return ctx.agent.env.get_fs() if self.env is None else self.env.get_fs()

    def _resolve_path(self, path: str, ctx: AgentContext) -> str:
        """Resolve a potentially relative path to an absolute path."""
        cwd: str | None = None
        if self.cwd:
            cwd = self.cwd
        elif self.env and self.env.cwd:
            cwd = self.env.cwd
        elif ctx.agent.env and ctx.agent.env.cwd:
            cwd = ctx.agent.env.cwd

        if cwd and not (path.startswith("/") or (len(path) > 1 and path[1] == ":")):
            return str(Path(cwd) / path)
        return path

    async def _list_directory(
        self,
        ctx: AgentContext,
        path: str,
        *,
        pattern: str = "*",
        exclude: list[str] | None = None,
        max_depth: int = 1,
    ) -> ToolResult:
        """List files in a directory with filtering support.

        Args:
            ctx: Agent context for event emission and filesystem access
            path: Base directory to list
            pattern: Glob pattern to match files against. Use "*.py" to match Python
                files in current directory only, or "**/*.py" to match recursively.
                The max_depth parameter limits how deep "**" patterns search.
            exclude: List of patterns to exclude (uses fnmatch against relative paths)
            max_depth: Maximum directory depth to search (default: 1 = current dir only).
                Only affects recursive "**" patterns.

        Returns:
            Markdown-formatted directory listing
        """
        from agentpool_toolsets.fsspec_toolset.helpers import format_directory_listing

        path = self._resolve_path(path, ctx)
        msg = f"Listing directory: {path}"
        await ctx.events.tool_call_start(title=msg, kind="read", locations=[path])

        try:
            fs = self._get_fs(ctx)
            # Check if path exists
            if not await fs._exists(path):
                error_msg = f"Path does not exist: {path}"
                await ctx.events.file_operation("list", path=path, success=False, error=error_msg)
                return ToolResult(
                    content=f"Error: {error_msg}",
                )

            # Build glob path
            glob_pattern = f"{path.rstrip('/')}/{pattern}"
            paths = await fs._glob(glob_pattern, maxdepth=max_depth, detail=True)

            files: list[dict[str, Any]] = []
            dirs: list[dict[str, Any]] = []

            # Safety check - prevent returning too many items
            total_found = len(paths)
            if total_found > self.max_items:
                suggestions = []
                if pattern == "*":
                    suggestions.append("Use a more specific pattern like '*.py', '*.txt', etc.")
                if max_depth > 1:
                    suggestions.append(f"Reduce max_depth from {max_depth} to 1 or 2.")
                if not exclude:
                    suggestions.append("Use exclude parameter to filter out unwanted directories.")

                suggestion_text = " ".join(suggestions) if suggestions else ""
                error_msg = f"Error: Too many items ({total_found:,}). {suggestion_text}"
                return ToolResult(
                    content=error_msg,
                )

            for file_path, file_info in paths.items():
                rel_path = os.path.relpath(str(file_path), path)

                # Skip excluded patterns
                if exclude and any(fnmatch(rel_path, pat) for pat in exclude):
                    continue

                # Use type from glob detail info, falling back to isdir only if needed
                is_dir = await is_directory(fs, file_path, entry_type=file_info.get("type"))

                item_info = {
                    "name": Path(file_path).name,
                    "path": file_path,
                    "relative_path": rel_path,
                    "size": file_info.get("size", 0),
                    "type": "directory" if is_dir else "file",
                }
                if "mtime" in file_info:
                    item_info["modified"] = file_info["mtime"]

                if is_dir:
                    dirs.append(item_info)
                else:
                    files.append(item_info)

            await ctx.events.file_operation("list", path=path, success=True)
            result = format_directory_listing(path, dirs, files, pattern)
            if len(files) + len(dirs) >= self.max_items:  # The listing stopped at the limit
                result += f"\n\n[Listing truncated at {self.max_items} items]"
            # Emit formatted content for UI display
            from agentpool.agents.events import TextContentItem

            await ctx.events.tool_call_progress(
                title=f"Listed: {path}",
                items=[TextContentItem(text=result)],
                replace_content=True,
            )
        except (OSError, ValueError, FileNotFoundError) as e:
            await ctx.events.file_operation("list", path=path, success=False, error=str(e))
            error_msg = (
                f"Error: Could not list directory: {path}. Ensure path is absolute and exists."
            )
            return ToolResult(
                content=error_msg,
            )
        else:
            return ToolResult(content=result)
