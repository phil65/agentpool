"""Grep search tool implementation."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.tools.base import Tool, ToolResult


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from exxec import ExecutionEnvironment
    from fsspec.asyn import AsyncFileSystem

    from agentpool_toolsets.fsspec_toolset.grep import GrepBackend


logger = get_logger(__name__)


@dataclass
class GrepTool(Tool[ToolResult]):
    """Search file contents for patterns using grep.

    A standalone tool for searching file contents with:
    - Regex pattern matching
    - File filtering with glob patterns
    - Context lines before/after matches
    - Subprocess grep (ripgrep/grep) or pure Python fallback
    - Configurable output limits

    Use create_grep_tool() factory for convenient instantiation.
    """

    # Tool-specific configuration
    env: ExecutionEnvironment | None = None
    """Execution environment to use. Falls back to agent.env if not set."""

    fs: AsyncFileSystem | None = None
    """Filesystem to operate on (takes precedence over ``env``).

    Set for a custom filesystem: the tool then never touches the agent's
    environment (no host filesystem, cwd or subprocesses).
    """

    cwd: str | None = None
    """Working directory for resolving relative paths."""

    max_output_kb: int = 64
    """Maximum output size in KB."""

    use_subprocess_grep: bool = True
    """Use ripgrep/grep subprocess if available (faster for large codebases)."""

    _grep_backend: GrepBackend | None = field(default=None, init=False)
    """Cached grep backend detection."""

    def get_callable(self) -> Callable[..., Awaitable[ToolResult]]:
        """Return the grep method as the callable."""
        return self._grep

    def _get_fs(self, ctx: AgentContext) -> AsyncFileSystem:
        """Get the filesystem: explicit fs, then env, then the agent's env."""
        if self.fs is not None:
            return self.fs
        return ctx.agent.env.get_fs() if self.env is None else self.env.get_fs()

    def _resolve_path(self, path: str, ctx: AgentContext) -> str:
        """Resolve a potentially relative path to an absolute path."""
        cwd: str | None = None
        if self.cwd:
            cwd = self.cwd
        elif self.env and self.env.cwd:
            cwd = self.env.cwd
        elif self.fs is None and ctx.agent.env and ctx.agent.env.cwd:
            cwd = ctx.agent.env.cwd

        if cwd and not (path.startswith("/") or (len(path) > 1 and path[1] == ":")):
            return str(Path(cwd) / path)
        return path

    async def _grep(
        self,
        ctx: AgentContext,
        pattern: str,
        path: str,
        *,
        file_pattern: str = "**/*",
        case_sensitive: bool = False,
        max_matches: int = 100,
        context_lines: int = 0,
    ) -> ToolResult:
        """Search file contents for a pattern.

        Args:
            ctx: Agent context for event emission and filesystem access
            pattern: Regex pattern to search for
            path: Base directory to search in
            file_pattern: Glob pattern to filter files (e.g. "**/*.py")
            case_sensitive: Whether search is case-sensitive
            max_matches: Maximum number of matches to return
            context_lines: Number of context lines before/after match

        Returns:
            Grep results as formatted text
        """
        from agentpool_toolsets.fsspec_toolset.grep import (
            DEFAULT_EXCLUDE_PATTERNS,
            detect_grep_backend,
            grep_with_fsspec,
            grep_with_subprocess,
        )

        resolved_path = self._resolve_path(path, ctx)
        msg = f"Searching for {pattern!r} in {resolved_path}"
        await ctx.events.tool_call_start(title=msg, kind="search", locations=[resolved_path])

        max_output_bytes = self.max_output_kb * 1024
        result: dict[str, Any] | None = None

        try:
            # Try subprocess grep if configured and available
            if self.use_subprocess_grep:
                # Get execution environment for running grep command. A custom fs
                # without an env is searched in-process only: a subprocess would
                # search the agent's (host) filesystem instead.
                env = self.env or (ctx.agent.env if self.fs is None else None)
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
                            max_output_bytes=max_output_bytes,
                            exclude_patterns=DEFAULT_EXCLUDE_PATTERNS,
                            use_gitignore=True,
                            context_lines=context_lines,
                            file_pattern=file_pattern,
                        )

            # Fallback to fsspec grep if subprocess didn't work
            if result is None or "error" in result:
                fs = self._get_fs(ctx)
                result = await grep_with_fsspec(
                    fs=fs,
                    pattern=pattern,
                    path=resolved_path,
                    file_pattern=file_pattern,
                    case_sensitive=case_sensitive,
                    max_matches=max_matches,
                    max_output_bytes=max_output_bytes,
                    context_lines=context_lines,
                )

            if "error" in result:
                error_msg = f"Error: {result['error']}"
                return ToolResult(
                    content=error_msg,
                )

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
            from agentpool.agents.events import TextContentItem

            await ctx.events.tool_call_progress(
                title=f"Found {match_count} matches",
                items=[TextContentItem(text=output)],
                replace_content=True,
            )
        except Exception as e:  # noqa: BLE001
            error_msg = f"Error: Grep failed: {e}"
            return ToolResult(
                content=error_msg,
            )
        else:
            return ToolResult(
                content=output,
            )
