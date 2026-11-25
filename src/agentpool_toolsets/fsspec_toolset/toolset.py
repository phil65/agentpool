"""FSSpec filesystem toolset implementation."""

from __future__ import annotations

import contextlib
from fnmatch import fnmatch
import os
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

import anyio
from exxec.base import ExecutionEnvironment
from pydantic_ai import (
    BinaryContent,
    ModelResponse,
    ModelRetry,
    PartDeltaEvent,
    PartStartEvent,
    RunContext,
    TextPart,
    TextPartDelta,
    ToolCallPart,
)
from sublime_search import replace_content
from upathtools import is_directory

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.mime_utils import guess_type, is_binary_content, is_binary_mime
from agentpool.resource_providers import ResourceProvider
from agentpool.tool_impls.delete_path import create_delete_path_tool
from agentpool.tool_impls.download_file import create_download_file_tool
from agentpool.tool_impls.grep import create_grep_tool
from agentpool.tool_impls.list_directory import create_list_directory_tool
from agentpool.tool_impls.read import create_read_tool
from agentpool.tools.base import ToolResult  # noqa: TC001
from agentpool.utils.diffs import get_changed_line_numbers
from agentpool_toolsets.fsspec_toolset.diagnostics import (
    DiagnosticsConfig,
    DiagnosticsManager,
    format_diagnostics_table,
)
from agentpool_toolsets.fsspec_toolset.helpers import (
    format_directory_listing,
    truncate_lines,
)
from agentpool_toolsets.fsspec_toolset.streaming_diff_parser import (
    NewTextChunk,
    OldTextChunk,
    StreamingDiffParser,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    import fsspec
    from fsspec.asyn import AsyncFileSystem
    from pydantic_ai import ModelRequest

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.common_types import ModelType
    from agentpool.messaging import MessageHistory
    from agentpool.prompts.conversion_manager import ConversionManager
    from agentpool.tools.base import Tool
    from agentpool_toolsets.fsspec_toolset.grep import GrepBackend


logger = get_logger(__name__)


class FSSpecTools(ResourceProvider):
    """Provider for fsspec filesystem tools.

    NOTE: The ACP execution environment used handles the Terminal events of the protocol,
    the toolset should deal with the ToolCall events for UI display purposes.
    """

    def __init__(
        self,
        source: fsspec.AbstractFileSystem | ExecutionEnvironment | None = None,
        name: str | None = None,
        cwd: str | None = None,
        edit_model: ModelType | None = None,
        converter: ConversionManager | None = None,
        max_file_size_kb: int = 64,
        max_grep_output_kb: int = 64,
        use_subprocess_grep: bool = True,
        enable_diagnostics: bool = False,
        large_file_tokens: int = 12_000,
        map_max_tokens: int = 2048,
        edit_tool: Literal["simple", "batch", "agentic"] = "simple",
        max_image_size: int | None = 2000,
        max_image_bytes: int | None = None,
    ) -> None:
        """Initialize with an fsspec filesystem or execution environment.

        Args:
            source: Filesystem or execution environment to operate on.
                    If None, falls back to agent.env at runtime.
            name: Name for this toolset provider
            cwd: Optional cwd to resolve relative paths against
            edit_model: Optional edit model for text editing
            converter: Optional conversion manager for markdown conversion
            max_file_size_kb: Maximum file size in KB for read/write operations (default: 64KB)
            max_grep_output_kb: Maximum grep output size in KB (default: 64KB)
            use_subprocess_grep: Use ripgrep/grep subprocess if available (default: True)
            enable_diagnostics: Run LSP CLI diagnostics after file writes (default: False)
            large_file_tokens: Token threshold for switching to structure map (default: 12000)
            map_max_tokens: Maximum tokens for structure map output (default: 2048)
            edit_tool: Which edit variant to expose ("simple" or "batch")
            max_image_size: Max width/height for images in pixels. Larger images are
                auto-resized for better model compatibility. Set to None to disable.
            max_image_bytes: Max file size for images in bytes. Images exceeding this
                are compressed using progressive quality/dimension reduction.
                Default: 4.5MB (below Anthropic's 5MB limit).
        """
        from upathtools import to_async_fs

        if source is None:
            self._fs: AsyncFileSystem | None = None
            self.execution_env: ExecutionEnvironment | None = None
        elif isinstance(source, ExecutionEnvironment):
            self.execution_env = source
            self._fs = source.get_fs()
        else:
            self.execution_env = None
            self._fs = to_async_fs(source)
        super().__init__(name=name or f"file_access_{self._fs.protocol if self._fs else 'default'}")
        self.edit_model = edit_model
        self.cwd = cwd
        self.converter = converter
        self.max_file_size = max_file_size_kb * 1024  # Convert KB to bytes
        self.max_grep_output = max_grep_output_kb * 1024  # Convert KB to bytes
        self.use_subprocess_grep = use_subprocess_grep
        self._tools: list[Tool] | None = None
        self._grep_backend: GrepBackend | None = None
        self._enable_diagnostics = enable_diagnostics
        self._diagnostics: DiagnosticsManager | None = None
        self._large_file_tokens = large_file_tokens
        self._map_max_tokens = map_max_tokens
        self._edit_tool = edit_tool
        self._max_image_size = max_image_size
        self._max_image_bytes = max_image_bytes

    def _get_fs(self, agent_ctx: AgentContext) -> AsyncFileSystem:
        """Get filesystem, falling back to agent's env if not set."""
        return agent_ctx.agent.env.get_fs() if self._fs is None else self._fs

    def _get_diagnostics_manager(self, agent_ctx: AgentContext) -> DiagnosticsManager | None:
        """Get or create the diagnostics manager."""
        if not self._enable_diagnostics:
            return None
        if self._diagnostics is None:
            env = self.execution_env or agent_ctx.agent.env
            # Default to rust-only for fast feedback after edits
            config = DiagnosticsConfig(rust_only=True, max_servers_per_language=1)
            self._diagnostics = DiagnosticsManager(env, config=config)
        return self._diagnostics

    async def _run_diagnostics(self, agent_ctx: AgentContext, path: str) -> str | None:
        """Run diagnostics on a file if enabled.

        Returns formatted diagnostics string if issues found, None otherwise.
        """
        mgr = self._get_diagnostics_manager(agent_ctx)
        if mgr is None:
            return None
        result = await mgr.run_for_file(path)
        if result.diagnostics:
            return format_diagnostics_table(result.diagnostics)
        return None

    async def _get_file_map(
        self, path: str, agent_ctx: AgentContext, content: str | None = None
    ) -> str | None:
        """Get structure map for a large file if language is supported.

        Args:
            path: Absolute file path (for language detection)
            agent_ctx: Agent context for filesystem access
            content: Optional pre-loaded file content (avoids duplicate read)

        Returns:
            Structure map string or None if language not supported
        """
        from agentpool.repomap import generate_file_outline

        # Use centralized outline generation
        fs = self._get_fs(agent_ctx)
        return await generate_file_outline(
            path, fs=fs, content=content, max_tokens=self._map_max_tokens
        )

    def _resolve_path(self, path: str, agent_ctx: AgentContext) -> str:
        """Resolve a potentially relative path to an absolute path.

        Gets cwd from self.cwd, execution_env.cwd, or agent.env.cwd.
        If cwd is set and path is relative, resolves relative to cwd.
        Otherwise returns the path as-is.
        """
        # Get cwd: explicit toolset cwd > execution_env.cwd > agent.env.cwd
        cwd: str | None = None
        if self.cwd:
            cwd = self.cwd
        elif self.execution_env and self.execution_env.cwd:
            cwd = self.execution_env.cwd
        elif agent_ctx.agent.env and agent_ctx.agent.env.cwd:
            cwd = agent_ctx.agent.env.cwd

        if cwd and not (path.startswith("/") or (len(path) > 1 and path[1] == ":")):
            return str(Path(cwd) / path)
        return path

    async def get_tools(self) -> Sequence[Tool]:
        """Get filesystem tools."""
        if self._tools is not None:
            return self._tools
        # Create standalone tools with toolset's configuration
        list_dir_tool = create_list_directory_tool(env=self.execution_env, cwd=self.cwd)
        read_tool = create_read_tool(
            env=self.execution_env,
            converter=self.converter,  # Pass converter for automatic markdown conversion
            cwd=self.cwd,
            max_file_size_kb=self.max_file_size // 1024,
            max_image_size=self._max_image_size,
            max_image_bytes=self._max_image_bytes,
            large_file_tokens=self._large_file_tokens,
            map_max_tokens=self._map_max_tokens,
        )

        grep_tool = create_grep_tool(
            env=self.execution_env,
            cwd=self.cwd,
            max_output_kb=self.max_grep_output // 1024,
            use_subprocess_grep=self.use_subprocess_grep,
        )

        delete_tool = create_delete_path_tool(env=self.execution_env, cwd=self.cwd)
        download_tool = create_download_file_tool(env=self.execution_env, cwd=self.cwd)
        self._tools = [
            list_dir_tool,
            read_tool,
            grep_tool,
            self.create_tool(self.write, category="edit"),
            delete_tool,
            download_tool,
        ]

        # Add edit tool based on config - mutually exclusive
        if self._edit_tool == "agentic":
            self._tools.append(self.create_tool(self.agentic_edit, category="edit"))
        elif self._edit_tool == "batch":
            self._tools.append(
                self.create_tool(self.edit_batch, category="edit", name_override="edit")
            )
        else:  # simple
            self._tools.append(self.create_tool(self.edit, category="edit"))

        # Add regex line editing tool
        self._tools.append(self.create_tool(self.regex_replace_lines, category="edit"))

        return self._tools

    async def list_directory(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        path: str,
        *,
        pattern: str = "*",
        exclude: list[str] | None = None,
        max_depth: int = 1,
    ) -> str:
        """List files in a directory with filtering support.

        Args:
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
        from agentpool.agents.events import TextContentItem

        path = self._resolve_path(path, agent_ctx)
        msg = f"Listing directory: {path}"
        await agent_ctx.events.tool_call_start(title=msg, kind="read", locations=[path])

        try:
            fs = self._get_fs(agent_ctx)
            # Check if path exists
            if not await fs._exists(path):
                error_msg = f"Path does not exist: {path}"
                await agent_ctx.events.file_operation(
                    "list", path=path, success=False, error=error_msg
                )
                return f"Error: {error_msg}"

            # Build glob path
            glob_pattern = f"{path.rstrip('/')}/{pattern}"
            paths = await fs._glob(glob_pattern, maxdepth=max_depth, detail=True)
            files: list[dict[str, Any]] = []
            dirs: list[dict[str, Any]] = []
            # Safety check - prevent returning too many items
            total_found = len(paths)
            if total_found > 500:  # noqa: PLR2004
                suggestions = []
                if pattern == "*":
                    suggestions.append("Use a more specific pattern like '*.py', '*.txt', etc.")
                if max_depth > 1:
                    suggestions.append(f"Reduce max_depth from {max_depth} to 1 or 2.")
                if not exclude:
                    suggestions.append("Use exclude parameter to filter out unwanted directories.")

                suggestion_text = " ".join(suggestions) if suggestions else ""
                return f"Error: Too many items ({total_found:,}). {suggestion_text}"

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

            await agent_ctx.events.file_operation("list", path=path, success=True)
            result = format_directory_listing(path, dirs, files, pattern)
            await agent_ctx.events.tool_call_progress(
                title=f"Listed: {path}",
                items=[TextContentItem(text=result)],
                replace_content=True,
            )
        except (OSError, ValueError, FileNotFoundError) as e:
            await agent_ctx.events.file_operation("list", path=path, success=False, error=str(e))
            return f"Error: Could not list directory: {path}. Ensure path is absolute and exists."
        else:
            return result

    async def read(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        path: str,
        encoding: str = "utf-8",
        line: int | None = None,
        limit: int | None = None,
    ) -> str | BinaryContent | list[str | BinaryContent]:
        """Read the context of a text file, or use vision capabilites to read images or documents.

        Args:
            path: File path to read
            encoding: Text encoding to use for text files (default: utf-8)
            line: Optional line number to start reading from (1-based, text files only)
            limit: Optional maximum number of lines to read (text files only)

        Returns:
            Text content for text files, BinaryContent for binary files (with optional
            dimension note as list when image was resized), or dict with error
        """
        from agentpool.agents.events import FileContentItem, LocationContentItem
        from agentpool.repomap import truncate_with_notice
        from agentpool_toolsets.fsspec_toolset.image_utils import resize_image_if_needed

        path = self._resolve_path(path, agent_ctx)
        msg = f"Reading file: {path}"
        # Emit progress - use 0 for line if negative (can't resolve until we read file)
        # LocationContentItem/ToolCallLocation require line >= 0 per ACP spec
        display_line = line if (line is not None and line > 0) else 0
        await agent_ctx.events.tool_call_progress(
            title=msg,
            items=[LocationContentItem(path=path, line=display_line)],
        )
        try:
            mime_type = guess_type(path)
            # Fast path: known binary MIME types (images, audio, video, etc.)
            if is_binary_mime(mime_type):
                data = await self._get_fs(agent_ctx)._cat_file(path)
                await agent_ctx.events.file_operation("read", path=path, success=True)
                mime = mime_type or "application/octet-stream"
                # Resize images if needed
                if self._max_image_size and mime.startswith("image/"):
                    data, mime, note = resize_image_if_needed(
                        data, mime, self._max_image_size, self._max_image_bytes
                    )
                    if note:
                        # Return resized image with dimension note for coordinate mapping
                        return [note, BinaryContent(data=data, media_type=mime, identifier=path)]
                return BinaryContent(data=data, media_type=mime, identifier=path)
            # Read content and probe for binary (git-style null byte detection)
            data = await self._get_fs(agent_ctx)._cat_file(path)
            if is_binary_content(data):
                # Binary file - return as BinaryContent for native model handling
                await agent_ctx.events.file_operation("read", path=path, success=True)
                mime = mime_type or "application/octet-stream"
                # Resize images if needed
                if self._max_image_size and mime.startswith("image/"):
                    data, mime, note = resize_image_if_needed(
                        data, mime, self._max_image_size, self._max_image_bytes
                    )
                    if note:
                        return [note, BinaryContent(data=data, media_type=mime, identifier=path)]
                return BinaryContent(data=data, media_type=mime, identifier=path)
            content = data.decode(encoding)

            # Check if file is too large and no targeted read requested
            tokens_approx = len(content) // 4
            if line is None and limit is None and tokens_approx > self._large_file_tokens:
                # Try structure map for supported languages
                map_result = await self._get_file_map(path, agent_ctx, content=content)
                if map_result:
                    await agent_ctx.events.file_operation("read", path=path, success=True)
                    content = map_result
                else:
                    # Fallback: head + tail for unsupported languages
                    content = truncate_with_notice(path, content)
                    await agent_ctx.events.file_operation("read", path=path, success=True)
            else:
                # Normal read with optional offset/limit
                lines = content.splitlines()
                offset = (line - 1) if line else 0
                result_lines, was_truncated = truncate_lines(
                    lines, offset, limit, self.max_file_size
                )
                content = "\n".join(result_lines)
                # Don't pass negative line numbers to events (ACP requires >= 0)
                display_line = line if (line and line > 0) else 0
                await agent_ctx.events.file_operation(
                    "read", path=path, success=True, line=display_line
                )
                if was_truncated:
                    content += f"\n\n[Content truncated at {self.max_file_size} bytes]"

        except Exception as e:  # noqa: BLE001
            await agent_ctx.events.file_operation("read", path=path, success=False, error=str(e))
            return f"error: Failed to read file {path}: {e}"
        else:
            # Emit file content for UI display (formatted at ACP layer)
            # Use non-negative line for display (negative lines are internal Python convention)
            display_start_line = max(1, line) if line and line > 0 else None
            await agent_ctx.events.tool_call_progress(
                title=f"Read: {path}",
                items=[FileContentItem(content=content, path=path, start_line=display_start_line)],
                replace_content=True,
            )
            # Return raw content for agent
            return content

    async def read_as_markdown(self, agent_ctx: AgentContext, path: str) -> str | dict[str, Any]:  # noqa: D417
        """Read file and convert to markdown text representation.

        Args:
            path: Path to read

        Returns:
            File content converted to markdown
        """
        from agentpool.agents.events import TextContentItem

        assert self.converter is not None, "Converter required for read_as_markdown"
        path = self._resolve_path(path, agent_ctx)
        msg = f"Reading file as markdown: {path}"
        await agent_ctx.events.tool_call_start(title=msg, kind="read", locations=[path])
        try:
            content = await self.converter.convert_file(path)
            await agent_ctx.events.file_operation("read", path=path, success=True)
            # Emit formatted content for UI display
            await agent_ctx.events.tool_call_progress(
                title=f"Read as markdown: {path}",
                items=[TextContentItem(text=content)],
                replace_content=True,
            )
        except Exception as e:  # noqa: BLE001
            await agent_ctx.events.file_operation("read", path=path, success=False, error=str(e))
            return f"Error: Failed to convert file {path}: {e}"
        else:
            return content

    async def write(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        path: str,
        content: str,
        mode: str = "w",
        overwrite: bool = False,
    ) -> str | ToolResult:
        """Write content to a file.

        Args:
            path: File path to write
            content: Content to write
            mode: Write mode ('w' for overwrite, 'a' for append)
            overwrite: Must be True to overwrite existing files (safety check)

        Returns:
            Success message or ToolResult with metadata
        """
        from agentpool.agents.events import DiffContentItem
        from agentpool.tools.base import ToolResult

        path = self._resolve_path(path, agent_ctx)
        msg = f"Writing file: {path}"
        await agent_ctx.events.tool_call_start(title=msg, kind="edit", locations=[path])
        content_bytes = len(content.encode("utf-8"))
        try:
            if mode not in ("w", "a"):
                msg = f"Invalid mode '{mode}'. Use 'w' (write) or 'a' (append)"
                await agent_ctx.events.file_operation("write", path=path, success=False, error=msg)
                return f"Error: {msg}"

            # Check size limit
            if content_bytes > self.max_file_size:
                msg = (
                    f"Content size ({content_bytes} bytes) exceeds maximum "
                    f"({self.max_file_size} bytes)"
                )
                await agent_ctx.events.file_operation("write", path=path, success=False, error=msg)
                return f"Error: {msg}"

            # Check if file exists and overwrite protection
            fs = self._get_fs(agent_ctx)
            file_exists = await fs._exists(path)

            if file_exists and mode == "w" and not overwrite:
                msg = (
                    f"File '{path}' already exists. To overwrite it, you must set overwrite=True. "
                    f"This is a safety measure to prevent accidental data loss."
                )
                await agent_ctx.events.file_operation("write", path=path, success=False, error=msg)
                return f"Error: {msg}"

            # What the file held (the diff's, and a revert's, starting point)
            previous: str | None = None
            if file_exists:
                try:
                    previous = await self._read(agent_ctx, path)
                except Exception:  # noqa: BLE001
                    previous = None  # Unreadable: shown as new, appended content as written
            if mode == "a" and previous is not None:
                content = previous + content

            await self._write(agent_ctx, path, content)
            await agent_ctx.events.tool_call_progress(
                title=f"Wrote: {path}",
                items=[DiffContentItem(path=path, old_text=previous, new_text=content)],
            )

            # Run diagnostics if enabled (include in message for agent)
            diagnostics_msg = ""
            if diagnostics_output := await self._run_diagnostics(agent_ctx, path):
                diagnostics_msg = f"\n\nDiagnostics:\n{diagnostics_output}"

            action = "Appended to" if mode == "a" and file_exists else "Wrote"
            success_msg = f"{action} {path} ({content_bytes} bytes){diagnostics_msg}"
            return ToolResult(content=success_msg)  # Agent sees content
        except Exception as e:  # noqa: BLE001
            await agent_ctx.events.file_operation("write", path=path, success=False, error=str(e))
            return f"Error: Failed to write file {path}: {e}"

    async def delete_path(  # noqa: D417
        self, agent_ctx: AgentContext, path: str, recursive: bool = False
    ) -> dict[str, Any]:
        """Delete a file or directory.

        Args:
            path: Path to delete
            recursive: Whether to delete directories recursively

        Returns:
            Dictionary with operation result
        """
        path = self._resolve_path(path, agent_ctx)
        msg = f"Deleting path: {path}"
        await agent_ctx.events.tool_call_start(title=msg, kind="delete", locations=[path])
        try:
            # Check if path exists and get its type
            fs = self._get_fs(agent_ctx)
            try:
                info = await fs._info(path)
                path_type = info.get("type", "unknown")
            except FileNotFoundError:
                msg = f"Path does not exist: {path}"
                await agent_ctx.events.file_operation("delete", path=path, success=False, error=msg)
                return {"error": msg}
            except (OSError, ValueError) as e:
                msg = f"Could not check path {path}: {e}"
                await agent_ctx.events.file_operation("delete", path=path, success=False, error=msg)
                return {"error": msg}

            if path_type == "directory":
                if not recursive:
                    try:
                        contents = await fs._ls(path)
                        if contents:  # Check if directory is empty
                            error_msg = (
                                f"Directory {path} is not empty. "
                                f"Use recursive=True to delete non-empty directories"
                            )

                            # Emit failure event
                            await agent_ctx.events.file_operation(
                                "delete", path=path, success=False, error=error_msg
                            )

                            return {"error": error_msg}
                    except OSError, ValueError:
                        pass  # Continue with deletion attempt

                await fs._rm(path, recursive=recursive)
            else:  # It's a file
                await fs._rm(path)  # or _rm_file?

        except Exception as e:  # noqa: BLE001
            await agent_ctx.events.file_operation("delete", path=path, success=False, error=str(e))
            return {"error": f"Failed to delete {path}: {e}"}
        else:
            result = {"path": path, "deleted": True, "type": path_type, "recursive": recursive}
            await agent_ctx.events.file_operation("delete", path=path, success=True)
            return result

    async def edit(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        path: str,
        old_string: str,
        new_string: str,
        description: str,
        replace_all: bool = False,
        line_hint: int | None = None,
    ) -> str | ToolResult:
        r"""Edit a file by replacing specific content with smart matching.

        Uses sophisticated matching strategies to handle whitespace, indentation,
        and other variations. Shows the changes as a diff in the UI.

        Args:
            path: File path (absolute or relative to session cwd)
            old_string: Text content to find and replace
            new_string: Text content to replace it with
            description: Short Human-readable description of what the edit accomplishes
            replace_all: Whether to replace all occurrences (default: False)
            line_hint: Line number hint to disambiguate when multiple matches exist.
                If the pattern matches multiple locations, the match closest to this
                line will be used. Useful after getting a "multiple matches" error.

        Returns:
            Success message with edit summary
        """
        return await self.edit_batch(
            agent_ctx,
            path,
            replacements=[(old_string, new_string)],
            description=description,
            replace_all=replace_all,
            line_hint=line_hint,
        )

    async def edit_batch(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        path: str,
        replacements: list[tuple[str, str]],
        description: str,
        replace_all: bool = False,
        line_hint: int | None = None,
    ) -> str | ToolResult:
        r"""Edit a file by applying multiple replacements in one operation.

        Uses sophisticated matching strategies to handle whitespace, indentation,
        and other variations. Shows the changes as a diff in the UI.

        Replacements are applied sequentially, so later replacements see the result
        of earlier ones. Each old_string must uniquely match one location (unless
        replace_all=True). If a pattern matches multiple locations, include more
        surrounding context to disambiguate.

        Args:
            path: File path (absolute or relative to session cwd)
            replacements: List of (old_string, new_string) tuples to apply sequentially.
                IMPORTANT: Must be a list of pairs, like:
                  [("old text", "new text"), ("another old", "another new")]

                Each old_string should include enough context to uniquely identify
                the target location. For multi-line edits, include the full block.
            description: Short Human-readable description of what the edit accomplishes
            replace_all: Whether to replace all occurrences of each pattern (default: False)
            line_hint: Line number hint to disambiguate when multiple matches exist.
                Only applies when there is a single replacement. If the pattern matches
                multiple locations, the match closest to this line will be used.

        Returns:
            Success message with edit summary

        Example:
            replacements=[
                ("def old_name(", "def new_name("),
                ("old_name()", "new_name()"),  # Update call sites
            ]
        """
        from agentpool.tools.base import ToolResult

        path = self._resolve_path(path, agent_ctx)
        msg = f"Editing file: {path} ({description})"
        await agent_ctx.events.tool_call_start(title=msg, kind="edit", locations=[path])

        if not replacements:
            return "Error: replacements list cannot be empty"

        for old_str, new_str in replacements:
            if old_str == new_str:
                return f"Error: old_string and new_string must be different: {old_str!r}"

        try:  # Read current file content
            original_content = await self._read(agent_ctx, path)
            # Apply all replacements sequentially
            new_content = original_content
            # line_hint only makes sense for single replacements
            hint = line_hint if len(replacements) == 1 else None
            for old_str, new_str in replacements:
                try:
                    result = replace_content(
                        new_content, old_str, new_str, replace_all, line_hint=hint
                    )
                    new_content = result.content
                except ValueError as e:
                    error_msg = f"Edit failed on replacement {old_str!r}: {e}"
                    await agent_ctx.events.file_operation(
                        "edit", path=path, success=False, error=error_msg
                    )
                    return error_msg

            await self._write(agent_ctx, path, new_content)
            success_msg = f"Successfully edited {Path(path).name}: {description}"
            changed_line_numbers = get_changed_line_numbers(original_content, new_content)
            if lines_changed := len(changed_line_numbers):
                success_msg += f" ({lines_changed} lines changed)"

            await agent_ctx.events.file_edit_progress(
                path=path,
                old_text=original_content,
                new_text=new_content,
                status="completed",
            )

            # Run diagnostics if enabled
            if diagnostics_output := await self._run_diagnostics(agent_ctx, path):
                success_msg += f"\n\nDiagnostics:\n{diagnostics_output}"
        except Exception as e:  # noqa: BLE001
            error_msg = f"Error editing file: {e}"
            await agent_ctx.events.file_operation("edit", path=path, success=False, error=error_msg)
            return error_msg
        else:
            # Ensure content ends with newline for proper diff formatting
            return ToolResult(content=success_msg)

    async def regex_replace_lines(
        self,
        agent_ctx: AgentContext,
        path: str,
        start: int | str,
        end: int | str,
        pattern: str,
        replacement: str,
        *,
        count: int = 0,
    ) -> str:
        r"""Apply regex replacement to a line range specified by line numbers or text markers.

        Applies ``re.subn(pattern, replacement, line, count=count)`` to each line
        individually within the specified range. Because matching is per-line,
        avoid patterns that match the empty string (e.g. ``.*``, ``[\s\S]*``,
        ``\d*``) — ``subn`` will match both the content and the trailing empty
        string, duplicating the replacement.

        Useful for systematic edits:
        - Remove/add indentation
        - Comment/uncomment blocks
        - Rename variables within scope
        - Delete line ranges

        Args:
            agent_ctx: Agent execution context
            path: File path to edit
            start: Start of range - int (1-based line number) or str (unique text marker)
            end: End of range - int (1-based line number) or str (first occurrence after start)
            pattern: Regex pattern to search for within the range
            replacement: Replacement string (supports \1, \2 capture groups; empty removes)
            count: Max replacements per line (0 = unlimited)

        Returns:
            Success message with statistics

        Examples:
            # Remove a function
            regex_replace_lines(ctx, "file.py", "def old_func(", "    return", r".+\n", "")

            # Indent by line numbers
            regex_replace_lines(ctx, "file.py", 10, 20, r"^", "    ")

            # Uncomment a section
            regex_replace_lines(ctx, "file.py", "# START", "# END", r"^# ", "")
        """
        path = self._resolve_path(path, agent_ctx)
        msg = f"Regex editing file: {path}"
        await agent_ctx.events.tool_call_start(title=msg, kind="edit", locations=[path])

        try:
            # Read original content
            original_content = await self._read(agent_ctx, path)
            lines = original_content.splitlines(keepends=True)
            total_lines = len(lines)
            match start:
                case int() if start < 1:
                    raise ValueError(f"start line must be >= 1, got {start}")  # noqa: TRY301
                case int():
                    start_line = start
                case _:  # Find unique occurrence of string (raises ValueError if not found/unique)
                    start_line = self._find_unique_line(lines, start, "start")

            match end:
                case int() if end < start_line:
                    raise ValueError(f"end line {end} must be >= start line {start_line}")  # noqa: TRY301
                case int():
                    end_line = end
                case _:  # Find unique occurrence of string (raises ValueError if not found/unique)
                    end_line = self._find_first_after(lines, end, start_line, "end")

            # Validate range
            if end_line > total_lines:
                raise ValueError(f"end_line {end_line} exceeds file length {total_lines}")  # noqa: TRY301

            # Convert to 0-based indexing for array access
            start_idx = start_line - 1
            end_idx = end_line  # end_line is inclusive, but list slice is exclusive
            # Compile regex pattern
            regex = re.compile(pattern)
            # Guard against patterns that match empty strings (e.g. .*, [\s\S]*,
            # \d*). These cause subn to replace both the actual content AND the
            # empty string after it on every line, duplicating the replacement.
            if regex.match("") is not None and replacement:
                raise ModelRetry(  # noqa: TRY301
                    f"Pattern {pattern!r} matches the empty string, which causes "
                    "duplicate replacements per line. Use a non-optional quantifier "
                    "(e.g. '.+' instead of '.*') or an anchored pattern (e.g. '^')."
                )
            # Apply replacements to the specified line range
            modified_count = 0
            replacement_count = 0
            for i in range(start_idx, end_idx):
                original = lines[i]
                modified, num_subs = regex.subn(replacement, original, count=count)
                if num_subs > 0:
                    lines[i] = modified
                    modified_count += 1
                    replacement_count += num_subs

            # Build new content
            new_content = "".join(lines)
            # Write back
            await self._write(agent_ctx, path, new_content)
            # Build success message
            success_msg = (
                f"Successfully applied regex to lines {start_line}-{end_line} in {Path(path).name}"
            )
            if modified_count > 0:
                success_msg += (
                    f" ({modified_count} lines modified, {replacement_count} replacements)"
                )

            # Emit file edit event for diff display
            await agent_ctx.events.file_edit_progress(
                path=path,
                old_text=original_content,
                new_text=new_content,
                status="completed",
            )

            # Run diagnostics if enabled
            if diagnostics_output := await self._run_diagnostics(agent_ctx, path):
                success_msg += f"\n\nDiagnostics:\n{diagnostics_output}"
        except Exception as e:  # noqa: BLE001
            error_msg = f"Error applying regex to file: {e}"
            await agent_ctx.events.file_operation("edit", path=path, success=False, error=error_msg)
            return error_msg
        else:
            return success_msg

    @staticmethod
    def _find_unique_line(lines: list[str], search_text: str, param_name: str) -> int:
        """Find unique occurrence of text in lines.

        Args:
            lines: File lines
            search_text: Text to search for
            param_name: Parameter name for error messages

        Returns:
            Line number (1-based)

        Raises:
            ValueError: If text not found or matches multiple lines
        """
        matches = []
        for i, line in enumerate(lines, start=1):
            if search_text in line:
                matches.append(i)

        if not matches:
            msg = f"{param_name} text not found: {search_text!r}"
            raise ValueError(msg)
        if len(matches) > 1:
            match_lines = ", ".join(str(m) for m in matches[:5])
            more = f" and {len(matches) - 5} more" if len(matches) > 5 else ""  # noqa: PLR2004
            msg = (
                f"{param_name} text matches multiple lines ({match_lines}{more}). "
                f"Include more context to make it unique."
            )
            raise ValueError(msg)

        return matches[0]

    @staticmethod
    def _find_first_after(
        lines: list[str], search_text: str, after_line: int, param_name: str
    ) -> int:
        """Find first occurrence of text after a given line.

        Args:
            lines: File lines
            search_text: Text to search for
            after_line: Line number to search after (1-based)
            param_name: Parameter name for error messages

        Returns:
            Line number (1-based)

        Raises:
            ValueError: If text not found after the specified line
        """
        for i in range(after_line - 1, len(lines)):
            if search_text in lines[i]:
                return i + 1

        msg = f"{param_name} text not found after line {after_line}: {search_text!r}"
        raise ValueError(msg)

    async def grep(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        pattern: str,
        path: str,
        *,
        file_pattern: str = "**/*",
        case_sensitive: bool = False,
        max_matches: int = 100,
        context_lines: int = 0,
    ) -> str:
        """Search file contents for a pattern.

        Args:
            pattern: Regex pattern to search for
            path: Base directory to search in
            file_pattern: Glob pattern to filter files (e.g. "**/*.py")
            case_sensitive: Whether search is case-sensitive
            max_matches: Maximum number of matches to return
            context_lines: Number of context lines before/after match

        Returns:
            Grep results as formatted text
        """
        from agentpool.agents.events import TextContentItem
        from agentpool_toolsets.fsspec_toolset.grep import (
            DEFAULT_EXCLUDE_PATTERNS,
            detect_grep_backend,
            grep_with_fsspec,
            grep_with_subprocess,
        )

        resolved_path = self._resolve_path(path, agent_ctx)
        msg = f"Searching for {pattern!r} in {resolved_path}"
        await agent_ctx.events.tool_call_start(title=msg, kind="search", locations=[resolved_path])

        result: dict[str, Any] | None = None
        try:
            # Try subprocess grep if configured and available
            if self.use_subprocess_grep:
                # Get execution environment for running grep command
                env = self.execution_env or agent_ctx.agent.env
                if env is not None:
                    # Detect and cache grep backend
                    if self._grep_backend is None:
                        self._grep_backend = await detect_grep_backend(env)
                    # Only use subprocess if we have a real grep backend
                    if self._grep_backend != "fsspec":
                        result = await grep_with_subprocess(
                            env=env,
                            pattern=pattern,
                            path=resolved_path,
                            backend=self._grep_backend,
                            case_sensitive=case_sensitive,
                            max_matches=max_matches,
                            max_output_bytes=self.max_grep_output,
                            exclude_patterns=DEFAULT_EXCLUDE_PATTERNS,
                            use_gitignore=True,
                            context_lines=context_lines,
                        )

            # Fallback to fsspec grep if subprocess didn't work
            if result is None or "error" in result:
                fs = self._get_fs(agent_ctx)
                result = await grep_with_fsspec(
                    fs=fs,
                    pattern=pattern,
                    path=resolved_path,
                    file_pattern=file_pattern,
                    case_sensitive=case_sensitive,
                    max_matches=max_matches,
                    max_output_bytes=self.max_grep_output,
                    context_lines=context_lines,
                )

            if "error" in result:
                return f"Error: {result['error']}"

            # Format output
            matches = result.get("matches", "")
            match_count = result.get("match_count", 0)
            was_truncated = result.get("was_truncated", False)

            if not matches:
                output = f"No matches found for pattern '{pattern}'"
            else:
                output = f"Found {match_count} matches:\n\n{matches}"
                if was_truncated:
                    output += "\n\n[Results truncated]"
            # Emit formatted content for UI display
            await agent_ctx.events.tool_call_progress(
                title=f"Found {match_count} matches",
                items=[TextContentItem(text=output)],
                replace_content=True,
            )
        except Exception as e:  # noqa: BLE001
            return f"Error: Grep failed: {e}"
        else:
            return output

    async def _read(self, agent_ctx: AgentContext, path: str, encoding: str = "utf-8") -> str:
        # with self.fs.open(path, "r", encoding="utf-8") as f:
        #     return f.read()
        val = await self._get_fs(agent_ctx)._cat(path)
        return val.decode() if isinstance(val, bytes) else val

    async def _write(self, agent_ctx: AgentContext, path: str, content: str | bytes) -> None:
        if isinstance(content, str):
            content = content.encode()
        await self._get_fs(agent_ctx)._pipe_file(path, content)

    async def download_file(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        url: str,
        target_dir: str = "downloads",
        chunk_size: int = 8192,
    ) -> dict[str, Any]:
        """Download a file from URL to the toolset's filesystem.

        Args:
            url: URL to download from
            target_dir: Directory to save the file (relative to cwd if set)
            chunk_size: Size of chunks to download

        Returns:
            Status information about the download
        """
        import httpx2

        start_time = time.time()
        # Resolve target directory
        target_dir = self._resolve_path(target_dir, agent_ctx)
        msg = f"Downloading: {url}"
        await agent_ctx.events.tool_call_start(title=msg, kind="read", locations=[url])
        # Extract filename from URL
        filename = Path(urlparse(url).path).name or "downloaded_file"
        full_path = f"{target_dir.rstrip('/')}/{filename}"

        try:
            fs = self._get_fs(agent_ctx)
            # Ensure target directory exists
            await fs._makedirs(target_dir, exist_ok=True)
            async with (
                httpx2.AsyncClient(verify=False) as client,
                client.stream("GET", url, timeout=30.0) as response,
            ):
                response.raise_for_status()
                total = int(i) if (i := response.headers["Content-Length"]) else None
                # Collect all data
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size):
                    data.extend(chunk)
                    size = len(data)

                    if total and (size % (chunk_size * 100) == 0 or size == total):
                        progress = size / total * 100
                        speed_mbps = (size / 1_048_576) / (time.time() - start_time)
                        progress_msg = f"\r{filename}: {progress:.1f}% ({speed_mbps:.1f} MB/s)"
                        await agent_ctx.events.progress(progress, 100, progress_msg)
                        await anyio.sleep(0)

                # Write to filesystem
                await self._write(agent_ctx, full_path, bytes(data))

            duration = time.time() - start_time
            size_mb = len(data) / 1_048_576

            await agent_ctx.events.file_operation("read", path=full_path, success=True)

            return {
                "path": full_path,
                "filename": filename,
                "size_bytes": len(data),
                "size_mb": round(size_mb, 2),
                "duration_seconds": round(duration, 2),
                "speed_mbps": round(size_mb / duration, 2) if duration > 0 else 0,
            }

        except httpx2.ConnectError as e:
            error_msg = f"Connection error downloading {url}: {e}"
            await agent_ctx.events.file_operation("read", path=url, success=False, error=error_msg)
            return {"error": error_msg}
        except httpx2.TimeoutException:
            error_msg = f"Timeout downloading {url}"
            await agent_ctx.events.file_operation("read", path=url, success=False, error=error_msg)
            return {"error": error_msg}
        except httpx2.HTTPStatusError as e:
            error_msg = f"HTTP error {e.response.status_code} downloading {url}"
            await agent_ctx.events.file_operation("read", path=url, success=False, error=error_msg)
            return {"error": error_msg}
        except Exception as e:  # noqa: BLE001
            error_msg = f"Error downloading {url}: {e!s}"
            await agent_ctx.events.file_operation("read", path=url, success=False, error=error_msg)
            return {"error": error_msg}

    async def agentic_edit(  # noqa: D417
        self,
        run_ctx: RunContext,
        agent_ctx: AgentContext,
        path: str,
        display_description: str,
        mode: Literal["edit", "create", "overwrite"] = "edit",
        matcher: Literal["zed", "default"] = "default",
    ) -> str:
        r"""Edit or create a file with streaming support.

        Use this tool for file modifications. Describe what changes you want
        and the tool will apply them progressively as they're generated.

        Args:
            path: File path (absolute or relative to session cwd)
            display_description: What edits to make - be specific about the changes.
                Examples: "Add error handling to the parse function",
                "Rename the 'foo' variable to 'bar' throughout",
                "Add a docstring to the MyClass class"
            mode: How to modify the file:
                - 'edit': Modify specific parts of existing file (default)
                - 'create': Create a new file (fails if file exists)
                - 'overwrite': Replace entire file content
            matcher: Internal matching algorithm - leave as default unless
                you have a reason to change it.

        Returns:
            Success message with edit summary
        """
        from agentpool.messaging import ChatMessage, MessageHistory

        path = self._resolve_path(path, agent_ctx)
        title = f"AI editing file: {path}"
        await agent_ctx.events.tool_call_start(title=title, kind="edit", locations=[path])
        await agent_ctx.events.file_operation("edit", path=path, success=True)

        match mode:
            case "create":
                original_content = ""
                prompt = _build_create_prompt(path, display_description)
            case "overwrite":
                original_content = await self._read(agent_ctx, path)
                prompt = _build_overwrite_prompt(path, display_description, original_content)
            case "edit":
                original_content = await self._read(agent_ctx, path)
                prompt = _build_edit_prompt(path, display_description, original_content)

        agent = agent_ctx.native_agent
        # Create forked message history from current conversation
        # This preserves full context while isolating the edit's messages
        # We need BOTH:
        # 1. Stored history (previous runs) from agent.conversation
        # 2. Current run messages from run_ctx.messages (not yet stored)
        stored_history = agent.conversation.get_history()
        # Build complete message list. Add stored history from previous runs
        all_messages: list[ModelRequest | ModelResponse] = []
        for chat_msg in stored_history:
            all_messages.extend(chat_msg.messages)

        # Add current run's messages (not yet in stored history)
        # But exclude the last message if it contains the current agentic_edit tool call
        # to avoid the sub-agent seeing "I'm calling agentic_edit" in its context

        for msg in run_ctx.messages:
            if isinstance(msg, ModelResponse):
                # Filter out the agentic_edit tool call from the last response
                filtered_parts = [
                    p
                    for p in msg.parts
                    if not (isinstance(p, ToolCallPart) and p.tool_name == "agentic_edit")
                ]
                if filtered_parts:
                    all_messages.append(ModelResponse(parts=filtered_parts))
            else:
                all_messages.append(msg)

            # Inject CachePoint to cache everything up to this point
            # if all_messages:
            #     cache_request: ModelRequest = ModelRequest(parts=[CachePoint()])
            #     all_messages.append(cache_request)

            # Wrap in a single ChatMessage for the forked history
            fork_history = MessageHistory(
                messages=[ChatMessage(messages=all_messages, role="user", content="")]
            )
        fork_history = MessageHistory()
        try:
            # Stream the edit using the same agent but with forked history
            if mode == "edit" and matcher == "zed":
                # TRUE STREAMING with Zed-style DP fuzzy matcher
                new_content = await self._stream_edit_with_matcher(
                    agent, prompt, fork_history, original_content, path, agent_ctx
                )
            elif mode == "edit":
                # TRUE STREAMING with our 9-strategy replace_content (default)
                new_content = await self._stream_edit_with_replace(
                    agent, prompt, fork_history, original_content, path, agent_ctx
                )
            else:
                # CREATE/OVERWRITE: Stream raw content directly
                new_content = await self._stream_raw_content(
                    agent, prompt, fork_history, original_content, path, agent_ctx
                )

            # Write the new content to file
            await self._write(agent_ctx, path, new_content)
            # Build success message
            original_lines = len(original_content.splitlines()) if original_content else 0
            new_lines = len(new_content.splitlines())
            if mode == "create":
                success_msg = f"Successfully created {Path(path).name} ({new_lines} lines)"
            else:
                success_msg = f"Successfully edited {Path(path).name} using AI agent"
                success_msg += f" ({original_lines} → {new_lines} lines)"
            # Send final completion update
            await agent_ctx.events.file_edit_progress(
                path=path,
                old_text=original_content,
                new_text=new_content,
                status="completed",
            )
        except Exception as e:  # noqa: BLE001
            error_msg = f"Error during agentic edit: {e}"
            await agent_ctx.events.file_operation("edit", path=path, success=False, error=error_msg)
            return error_msg
        else:
            return success_msg

    async def _stream_edit_with_matcher(  # noqa: PLR0915
        self,
        agent: BaseAgent,
        prompt: str,
        fork_history: MessageHistory,
        original_content: str,
        path: str,
        agent_ctx: AgentContext,
    ) -> str:
        """TRUE streaming edit using StreamingDiffParser + StreamingFuzzyMatcher.

        Parses diff incrementally, uses DP matcher to find locations as old_text
        streams, and applies new_text edits as they arrive.
        """
        from sublime_search import StreamingFuzzyMatcher

        parser = StreamingDiffParser()
        matcher = StreamingFuzzyMatcher(original_content)

        # Track current state
        edited_content = original_content
        pending_old_text: list[str] = []  # Track old text for prefix/suffix calculation
        pending_new_text: list[str] = []
        current_match_range = None

        async for node in agent.run_stream(
            prompt,
            message_history=fork_history,
            store_history=False,
        ):
            match node:
                case (
                    PartStartEvent(part=TextPart(content=chunk))
                    | PartDeltaEvent(delta=TextPartDelta(content_delta=chunk))
                ):
                    # Parse diff chunk and process events
                    for event in parser.push(chunk):
                        match event:
                            case OldTextChunk(chunk=chunk, line_hint=line_hint) if not event.done:
                                # Track old text for later prefix/suffix calculation
                                pending_old_text.append(chunk)
                                # Push to matcher for location resolution
                                if match_result := matcher.push(chunk, line_hint=line_hint):
                                    current_match_range = match_result
                            case OldTextChunk():
                                # Old text done - finalize location
                                if matches := matcher.finish():
                                    current_match_range = matches[0]
                                # Reset matcher for next hunk
                                matcher = StreamingFuzzyMatcher(edited_content)
                            case NewTextChunk(chunk=chunk) if not event.done:
                                pending_new_text.append(chunk)
                            case NewTextChunk():
                                # New text done - apply the edit if we have a location
                                if current_match_range and pending_new_text:
                                    new_text = "".join(pending_new_text)
                                    old_text = "".join(pending_old_text)

                                    # The matcher may find a larger range than our old_text
                                    # We need to preserve any prefix/suffix not in old_text
                                    matched_text = edited_content[
                                        current_match_range.start : current_match_range.end
                                    ]

                                    # Find where old_text appears in matched_text
                                    old_text_stripped = old_text.strip("\n")
                                    match_idx = matched_text.find(old_text_stripped)

                                    if match_idx >= 0:
                                        # Preserve prefix (e.g., blank lines before)
                                        prefix = matched_text[:match_idx]
                                        # Preserve suffix after old_text
                                        suffix_start = match_idx + len(old_text_stripped)
                                        suffix = matched_text[suffix_start:]
                                        # Build replacement with preserved prefix/suffix
                                        replacement = prefix + new_text.strip("\n") + suffix
                                    else:
                                        # Fallback: direct replacement
                                        replacement = new_text

                                    # Apply edit to content
                                    edited_content = (
                                        edited_content[: current_match_range.start]
                                        + replacement
                                        + edited_content[current_match_range.end :]
                                    )
                                    # Emit progress update
                                    await agent_ctx.events.file_edit_progress(
                                        path=path,
                                        old_text=original_content,
                                        new_text=edited_content,
                                        status="in_progress",
                                    )

                                # Reset for next hunk
                                pending_old_text = []
                                pending_new_text = []
                                current_match_range = None

        # Process any remaining content
        for event in parser.finish():
            match event:
                case OldTextChunk(chunk=chunk) if not event.done:
                    pending_old_text.append(chunk)
                    matcher.push(chunk, line_hint=event.line_hint)
                case OldTextChunk() if matches := matcher.finish():
                    current_match_range = matches[0]
                case NewTextChunk(chunk=chunk) if not event.done:
                    pending_new_text.append(chunk)
                case NewTextChunk() if current_match_range and pending_new_text:
                    new_text = "".join(pending_new_text)
                    old_text = "".join(pending_old_text)
                    matched_text = edited_content[
                        current_match_range.start : current_match_range.end
                    ]
                    old_text_stripped = old_text.strip("\n")
                    match_idx = matched_text.find(old_text_stripped)
                    if match_idx >= 0:
                        prefix = matched_text[:match_idx]
                        suffix_start = match_idx + len(old_text_stripped)
                        suffix = matched_text[suffix_start:]
                        replacement = prefix + new_text.strip("\n") + suffix
                    else:
                        replacement = new_text
                    edited_content = (
                        edited_content[: current_match_range.start]
                        + replacement
                        + edited_content[current_match_range.end :]
                    )

        return edited_content

    async def _stream_edit_with_replace(
        self,
        agent: BaseAgent,
        prompt: str,
        fork_history: MessageHistory,
        original_content: str,
        path: str,
        agent_ctx: AgentContext,
    ) -> str:
        """TRUE streaming edit using StreamingDiffParser + replace_content().

        Parses diff incrementally, uses our 9-strategy replace_content() to
        find locations and apply edits as each hunk completes.
        """
        parser = StreamingDiffParser()

        # Track current state
        edited_content = original_content
        pending_old_text: list[str] = []
        pending_new_text: list[str] = []

        async for node in agent.run_stream(
            prompt,
            message_history=fork_history,
            store_history=False,
        ):
            match node:
                case (
                    PartStartEvent(part=TextPart(content=chunk))
                    | PartDeltaEvent(delta=TextPartDelta(content_delta=chunk))
                ):
                    # Parse diff chunk and process events
                    for event in parser.push(chunk):
                        match event:
                            case OldTextChunk(chunk=chunk) if not event.done:
                                pending_old_text.append(chunk)
                            # When old_text is done, we just wait for new_text
                            case NewTextChunk(chunk=chunk) if not event.done:
                                pending_new_text.append(chunk)
                            case NewTextChunk() if pending_old_text and pending_new_text:
                                # Hunk complete - apply using replace_content
                                old_text = "".join(pending_old_text)
                                new_text = "".join(pending_new_text)
                                try:
                                    result = replace_content(
                                        edited_content,
                                        old_text,
                                        new_text,
                                        replace_all=False,
                                    )
                                    await agent_ctx.events.file_edit_progress(
                                        path=path,
                                        old_text=original_content,
                                        new_text=result.content,
                                        status="in_progress",
                                    )
                                except ValueError as e:
                                    # Log but continue - some hunks may fail
                                    logger.warning(
                                        "Streaming hunk failed",
                                        error=str(e),
                                        old_text=old_text[:50],
                                    )
                                pending_old_text = []
                                pending_new_text = []

                            case NewTextChunk():
                                # Reset for next hunk
                                pending_old_text = []
                                pending_new_text = []

        # Process any remaining content
        for event in parser.finish():
            if isinstance(event, OldTextChunk) and not event.done:
                pending_old_text.append(event.chunk)
            elif isinstance(event, NewTextChunk):
                if not event.done:
                    pending_new_text.append(event.chunk)
                elif pending_old_text and pending_new_text:
                    old_text = "".join(pending_old_text)
                    new_text = "".join(pending_new_text)
                    with contextlib.suppress(ValueError):
                        result = replace_content(
                            edited_content,
                            old_text,
                            new_text,
                            replace_all=False,
                        )
                        edited_content = result.content

        return edited_content

    async def _stream_raw_content(
        self,
        agent: BaseAgent,
        prompt: str,
        fork_history: MessageHistory,
        original_content: str,
        path: str,
        agent_ctx: AgentContext,
    ) -> str:
        """Stream raw content for create/overwrite modes.

        Emits progress updates as content streams in.
        """
        streamed_content = ""

        async for node in agent.run_stream(
            prompt,
            message_history=fork_history,
            store_history=False,
        ):
            match node:
                case (
                    PartStartEvent(part=TextPart(content=chunk))
                    | PartDeltaEvent(delta=TextPartDelta(content_delta=chunk))
                ):
                    streamed_content += chunk
                    # Emit periodic progress updates
                    await agent_ctx.events.file_edit_progress(
                        path=path,
                        old_text=original_content,
                        new_text=streamed_content,
                        status="in_progress",
                    )

        return streamed_content


def _build_create_prompt(path: str, description: str) -> str:
    """Build prompt for create mode."""
    return f"""Create a new file at {path} according to this description:

{description}

Output ONLY the complete file content. No explanations, no markdown code blocks, no formatting.
DO NOT use any tools. Just output the file content directly."""


def _build_overwrite_prompt(path: str, description: str, current_content: str) -> str:
    """Build prompt for overwrite mode."""
    return f"""Rewrite the file {path} according to this description:

{description}

<current_file_content>
{current_content}
</current_file_content>

Output ONLY the complete new file content. No explanations, no code blocks, no formatting.
DO NOT use any tools. Just output the file content directly."""


def _build_edit_prompt(path: str, description: str, current_content: str) -> str:
    """Build prompt for diff-based edit mode."""
    return f"""\
You MUST respond with edits in unified diff format. Output your changes inside <diff> tags.

# Diff Format Instructions

Use standard unified diff format with context lines:
- Lines starting with a space are context (unchanged)
- Lines starting with `-` are removed
- Lines starting with `+` are added
- Include 2-3 lines of context before and after each change
- Do NOT include line numbers or @@ headers - locations are matched by context

Example:
<diff>
 def existing_function():
-    old_implementation()
+    new_implementation()
     return result
</diff>

# Rules

- Context lines MUST exactly match the file content (including indentation)
- Include enough context to uniquely identify the location
- For multiple separate changes, include all hunks within a single <diff> block
- Separate hunks with a blank line
- Do not escape quotes or special characters
- Preserve exact indentation from the original file

<file_to_edit path="{path}">
{current_content}
</file_to_edit>

<edit_description>
{description}
</edit_description>

You MUST wrap your response in <diff>...</diff> tags.
DO NOT use any tools. Just output the diff directly."""


if __name__ == "__main__":

    async def main() -> None:
        from pydantic_ai import RunContext as PyAiContext, RunUsage
        from pydantic_ai.models.test import TestModel
        from upathtools import core

        from agentpool import Agent, AgentPool

        fs = core.filesystem("file")
        tools = FSSpecTools(fs, name="local_fs")
        async with AgentPool() as pool:
            agent = Agent(name="test", model="anthropic-max:claude-haiku-4-5")
            await pool.add_agent(agent)
            agent_ctx = agent.get_context()
            result = await tools.agentic_edit(
                PyAiContext(deps=None, model=TestModel(), usage=RunUsage()),
                agent_ctx,
                path="/home/phil65/dev/oss/agentpool/src/agentpool_toolsets/fsspec_toolset/toolset.py",
                display_description="Append a poem",
            )
            print(result)

    anyio.run(main)
