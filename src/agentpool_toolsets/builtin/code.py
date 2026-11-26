"""Provider for code formatting and linting tools."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from upathtools import is_directory

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider
from agentpool.utils.listing_filter import filter_listing_detail
from agentpool_toolsets.fsspec_toolset.diagnostics import (
    DiagnosticsManager,
    format_diagnostics_table,
    format_run_summary,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from exxec.base import ExecutionEnvironment
    from fsspec.asyn import AsyncFileSystem

    from agentpool.tools.base import Tool
    from agentpool_toolsets.fsspec_toolset.diagnostics import DiagnosticsConfig

logger = get_logger(__name__)

# Map file extensions to ast-grep language identifiers
EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".swift": "swift",
    ".lua": "lua",
    ".cs": "csharp",
}


def _substitute_metavars(match: Any, fix_pattern: str, source_code: str) -> str:
    """Substitute $METAVARS and $$$METAVARS in fix pattern with captured values."""
    result = fix_pattern
    # Handle $$$ multi-match metavars first (greedy match)
    for metavar in re.findall(r"\$\$\$([A-Z_][A-Z0-9_]*)", fix_pattern):
        if captured_list := match.get_multiple_matches(metavar):
            first = captured_list[0]
            last = captured_list[-1]
            start_idx = first.range().start.index
            end_idx = last.range().end.index
            original_span = source_code[start_idx:end_idx]
            result = result.replace(f"$$${metavar}", original_span)

    # Handle single $ metavars
    for metavar in re.findall(r"(?<!\$)\$([A-Z_][A-Z0-9_]*)", fix_pattern):
        if captured := match.get_match(metavar):
            result = result.replace(f"${metavar}", captured.text())

    return result


def _detect_language(path: str) -> str | None:
    """Detect ast-grep language from file extension."""
    suffix = Path(path).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(suffix)


class CodeTools(ResourceProvider):
    """Provider for code analysis and transformation tools."""

    def __init__(
        self,
        env: ExecutionEnvironment | None = None,
        name: str = "code",
        cwd: str | None = None,
        diagnostics_config: DiagnosticsConfig | None = None,
    ) -> None:
        """Initialize with an optional execution environment.

        Args:
            env: Execution environment to operate on. If None, falls back to agent.env
            name: Name for this toolset provider
            cwd: Optional cwd to resolve relative paths against (falls back to env.cwd)
            diagnostics_config: Configuration for diagnostic tools (server selection, etc.)
        """
        super().__init__(name=name)
        self._explicit_env = env
        self._explicit_cwd = cwd
        self._diagnostics_config = diagnostics_config
        self._tools: list[Tool] | None = None

    def _get_env(self, agent_ctx: AgentContext) -> ExecutionEnvironment:
        """Get execution environment (explicit or from agent)."""
        return self._explicit_env or agent_ctx.agent.env

    def _get_cwd(self, agent_ctx: AgentContext) -> str | None:
        """Get working directory (explicit, from env, or from agent.env)."""
        if self._explicit_cwd:
            return self._explicit_cwd
        env = self._get_env(agent_ctx)
        return env.cwd

    def _get_fs(self, agent_ctx: AgentContext) -> AsyncFileSystem:
        """Get filesystem (from env or fallback to local)."""
        env = self._get_env(agent_ctx)
        return env.get_fs()

    def _resolve_path(self, path: str, agent_ctx: AgentContext) -> str:
        """Resolve a potentially relative path to an absolute path.

        Gets cwd from explicit cwd, env.cwd, or agent.env.cwd.
        If cwd is set and path is relative, resolves relative to cwd.
        Otherwise returns the path as-is.
        """
        cwd = self._get_cwd(agent_ctx)
        if cwd and not (path.startswith("/") or (len(path) > 1 and path[1] == ":")):
            return str(Path(cwd) / path)
        return path

    async def get_tools(self) -> Sequence[Tool]:
        """Get code analysis tools."""
        if self._tools is not None:
            return self._tools

        self._tools = [self.create_tool(self.format_code, category="execute")]
        if importlib.util.find_spec("ast_grep_py"):
            self._tools.append(self.create_tool(self.ast_grep, category="search", idempotent=True))
        # Always register - checks for env at runtime (self.execution_env or agent.env).
        # It runs external checkers (ty, pyright, mypy, biome, ...) in the environment,
        # which may load project config and plugins: an execute tool that needs
        # confirmation in "per_tool" mode, not a read-only search tool.
        self._tools.append(
            self.create_tool(self.run_diagnostics, category="execute", requires_confirmation=True)
        )
        return self._tools

    async def format_code(  # noqa: D417
        self, agent_ctx: AgentContext, path: str, language: str | None = None
    ) -> str:
        """Format and lint a code file, returning a concise summary.

        Args:
            path: Path to the file to format
            language: Programming language (auto-detected from extension if not provided)

        Returns:
            Short status message about formatting/linting results
        """
        from anyenv.language_formatters import FormatterRegistry

        resolved = self._resolve_path(path, agent_ctx)
        fs = self._get_fs(agent_ctx)
        try:
            content = await fs._cat_file(resolved)
            code = content.decode("utf-8") if isinstance(content, bytes) else content
        except FileNotFoundError:
            return f"❌ File not found: {path}"

        registry = FormatterRegistry("local")
        registry.register_default_formatters()
        # Get formatter by language or detect from extension/content
        formatter = None
        if language:
            formatter = registry.get_formatter_by_language(language)
        if not formatter:
            detected = _detect_language(path)
            if detected:
                formatter = registry.get_formatter_by_language(detected)
        if not formatter:
            detected = registry.detect_language_from_content(code)
            if detected:
                formatter = registry.get_formatter_by_language(detected)
        if not formatter:
            return f"❌ Unsupported language: {language or 'unknown'}"

        try:
            result = await formatter.format_and_lint_string(code, fix=True)

            if result.success:
                # Write back if formatted
                if result.format_result.formatted and result.format_result.output:
                    await fs._pipe_file(resolved, result.format_result.output.encode("utf-8"))
                    changes = "formatted and saved"
                else:
                    changes = "no changes needed"
                lint_status = "clean" if result.lint_result.success else "has issues"
                duration = f"{result.total_duration:.2f}s"
                return f"✅ {path}: {changes}, {lint_status} ({duration})"

            errors = []
            if not result.format_result.success:
                errors.append(f"format: {result.format_result.error_type}")
            if not result.lint_result.success:
                errors.append(f"lint: {result.lint_result.error_type}")
            return f"❌ Failed: {', '.join(errors)}"

        except Exception as e:  # noqa: BLE001
            return f"❌ Error: {type(e).__name__}: {e}"

    async def ast_grep(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        path: str,
        rule: dict[str, Any],
        fix: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Search or transform code in a file using AST patterns.

        Uses ast-grep for structural code search and rewriting based on abstract
        syntax trees. More precise than regex - understands code structure.

        Args:
            path: Path to the file to analyze
            rule: AST matching rule dict (see examples below)
            fix: Optional replacement pattern using $METAVARS from the rule
            dry_run: If True, show changes without applying. If False, write changes.

        Returns:
            Dict with matches and optionally transformed code

        ## Pattern Syntax

        - `$NAME` - captures single node (identifier, expression, etc.)
        - `$$$ITEMS` - captures multiple nodes (arguments, statements, etc.)
        - Patterns match structurally, not textually

        ## Rule Keys

        | Key | Description | Example |
        |-----|-------------|---------|
        | pattern | Code pattern with metavars | `"print($MSG)"` |
        | kind | AST node type | `"function_definition"` |
        | regex | Regex on node text | `"^test_"` |
        | inside | Must be inside matching node | `{"kind": "class_definition"}` |
        | has | Must contain matching node | `{"pattern": "return"}` |
        | all | All rules must match | `[{"kind": "call"}, {"has": ...}]` |
        | any | Any rule must match | `[{"pattern": "print"}, {"pattern": "log"}]` |

        ## Examples

        **Find all print calls:**
        ```
        rule={"pattern": "print($MSG)"}
        ```

        **Find and replace console.log:**
        ```
        rule={"pattern": "console.log($MSG)"}
        fix="logger.info($MSG)"
        ```

        **Find functions containing await:**
        ```
        rule={
            "kind": "function_definition",
            "has": {"pattern": "await $EXPR"}
        }
        ```
        """
        from ast_grep_py import SgRoot

        resolved = self._resolve_path(path, agent_ctx)
        fs = self._get_fs(agent_ctx)

        # Detect language from extension
        language = _detect_language(path)
        if not language:
            return {"error": f"Cannot detect language for: {path}"}

        # Read file
        try:
            content = await fs._cat_file(resolved)
            code = content.decode("utf-8") if isinstance(content, bytes) else content
        except FileNotFoundError:
            return {"error": f"File not found: {path}"}

        root = SgRoot(code, language)
        node = root.root()
        matches = node.find_all(**rule)

        result: dict[str, Any] = {
            "path": path,
            "language": language,
            "match_count": len(matches),
            "matches": [
                {
                    "text": m.text(),
                    "range": {
                        "start": {"line": m.range().start.line, "column": m.range().start.column},
                        "end": {"line": m.range().end.line, "column": m.range().end.column},
                    },
                    "kind": m.kind(),
                }
                for m in matches
            ],
        }

        if fix and matches:
            edits = [m.replace(_substitute_metavars(m, fix, code)) for m in matches]
            fixed_code = node.commit_edits(edits)
            result["fixed_code"] = fixed_code
            result["dry_run"] = dry_run

            if not dry_run:
                await fs._pipe_file(resolved, fixed_code.encode("utf-8"))
                result["written"] = True

        return result

    async def run_diagnostics(self, agent_ctx: AgentContext, path: str) -> str:  # noqa: D417
        """Run LSP diagnostics (type checking, linting) on files.

        Uses available CLI diagnostic tools (pyright, mypy, ty, oxlint, biome, etc.)
        to check code for errors, warnings, and style issues.

        Args:
            path: Path to file or directory to check. For directories, checks all
                  supported files recursively.

        Returns:
            Formatted diagnostic output showing errors, warnings, and info messages.
            Returns a message if no diagnostics tools are available.

        Note:
            Only available when CodeTools is initialized with an ExecutionEnvironment.
            Automatically detects available diagnostic tools and runs appropriate
            ones based on file extensions.
        """
        resolved = self._resolve_path(path, agent_ctx)
        fs = self._get_fs(agent_ctx)
        env = self._get_env(agent_ctx)
        # Create diagnostics manager with config
        manager = DiagnosticsManager(env, config=self._diagnostics_config)

        # Progress callback that emits tool_call_progress events
        async def progress_callback(
            message: str,
            *,
            server_id: str | None = None,
            command: str | None = None,
            status: str = "running",
        ) -> None:
            items = []
            if command:
                items.append(f"```bash\n{command}\n```")
            await agent_ctx.events.tool_call_progress(message, items=items or None)

        # Check if path is directory or file
        try:
            is_dir = await fs._isdir(resolved)
        except Exception:  # noqa: BLE001
            is_dir = False

        if is_dir:
            # Collect all files in directory
            try:
                files = await fs._find(resolved, detail=True)
                files = await filter_listing_detail(fs, resolved, files)
                file_paths = [
                    p
                    for p, info in files.items()
                    if not await is_directory(fs, p, entry_type=info["type"])
                ]
            except Exception as e:  # noqa: BLE001
                return f"Error scanning directory: {e}"

            if not file_paths:
                return f"No files found in: {path}"

            result = await manager.run_for_files(file_paths, progress=progress_callback)
        else:
            # Single file
            try:
                result = await manager.run_for_file(resolved, progress=progress_callback)
            except FileNotFoundError:
                return f"File not found: {path}"
            except Exception as e:  # noqa: BLE001
                return f"Error running diagnostics: {e}"

        # Format output
        if not result.diagnostics:
            summary = format_run_summary(result)
            return f"No issues found. {summary}"

        formatted = format_diagnostics_table(result.diagnostics)
        summary = format_run_summary(result)
        await agent_ctx.events.tool_call_progress(summary, status="in_progress", items=[formatted])
        return f"{summary}\n\n{formatted}"
