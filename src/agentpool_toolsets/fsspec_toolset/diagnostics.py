"""Diagnostics orchestration for code analysis tools.

This module provides configurable orchestration for running diagnostic tools
(type checkers, linters) with support for:
- Server selection and filtering (rust-only, preferred servers, exclusions)
- Parallel execution
- Rich progress notifications with command details
- Extensible server definitions
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import shlex
import subprocess
import time
from typing import TYPE_CHECKING, Literal, Protocol

import anyenv

from agentpool.log import get_logger


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

logger = get_logger(__name__)


# =============================================================================
# Core Data Structures
# =============================================================================


@dataclass
class Diagnostic:
    """A single diagnostic message from a tool."""

    file: str
    line: int
    column: int
    severity: Literal["error", "warning", "info", "hint"]
    message: str
    source: str
    code: str | None = None
    end_line: int | None = None
    end_column: int | None = None


@dataclass
class DiagnosticRunResult:
    """Result from running a single diagnostic server."""

    server_id: str
    command: str
    diagnostics: list[Diagnostic]
    duration: float
    success: bool
    error: str | None = None


@dataclass
class DiagnosticsResult:
    """Combined result from running diagnostics."""

    diagnostics: list[Diagnostic]
    runs: list[DiagnosticRunResult]
    total_duration: float

    @property
    def success(self) -> bool:
        """Check if all runs succeeded."""
        return all(r.success for r in self.runs)

    @property
    def error_count(self) -> int:
        """Count of error-level diagnostics."""
        return sum(1 for d in self.diagnostics if d.severity == "error")

    @property
    def warning_count(self) -> int:
        """Count of warning-level diagnostics."""
        return sum(1 for d in self.diagnostics if d.severity == "warning")


@dataclass
class DiagnosticsConfig:
    """Configuration for diagnostic runs.

    Attributes:
        preferred_servers: Only run these servers (by ID). If None, run all available.
        excluded_servers: Never run these servers (by ID).
        rust_only: Shorthand to only run Rust-based tools (ty, oxlint, biome).
        max_servers_per_language: Limit servers per file extension (0 = unlimited).
        parallel: Run multiple servers in parallel.
        timeout: Timeout per server in seconds (0 = no timeout).
    """

    preferred_servers: list[str] | None = None
    excluded_servers: list[str] | None = None
    rust_only: bool = False
    max_servers_per_language: int = 0
    parallel: bool = True
    timeout: float = 30.0


# =============================================================================
# Server Definitions
# =============================================================================


@dataclass
class CLIDiagnosticConfig:
    """CLI configuration for running diagnostics."""

    command: str
    args: list[str]
    output_format: Literal["json", "text"] = "json"


@dataclass
class DiagnosticServer:
    """Base class for diagnostic server definitions.

    Attributes:
        id: Unique identifier for this server.
        extensions: File extensions this server handles (e.g., [".py", ".pyi"]).
        cli: CLI configuration for running diagnostics.
        rust_based: Whether this tool is implemented in Rust (fast).
        priority: Lower values run first when limiting servers per language.
    """

    id: str
    extensions: list[str]
    cli: CLIDiagnosticConfig
    rust_based: bool = False
    priority: int = 50

    def can_handle(self, extension: str) -> bool:
        """Check if this server handles the given file extension."""
        ext = extension if extension.startswith(".") else f".{extension}"
        return ext.lower() in [e.lower() for e in self.extensions]

    def build_argv(self, files: list[str]) -> list[str]:
        """Build the argument vector for running diagnostics.

        Each file name becomes its own argument (an argument that is exactly
        ``{files}`` expands to all files), so names are never re-split or
        interpreted by a shell.
        """
        argv = [self.cli.command]
        for arg in self.cli.args:
            if arg == "{files}":
                argv.extend(files)
            elif "{files}" in arg:
                argv.extend(arg.replace("{files}", f) for f in files)
            else:
                argv.append(arg)
        return argv

    def build_command(self, files: list[str], *, windows: bool = False) -> str:
        """Build the shell command line for running diagnostics.

        Every argument is quoted (``shlex.join`` on POSIX, ``list2cmdline`` on
        Windows), so file names containing spaces or shell metacharacters
        (``;``, ``$(...)``, backticks, ...) are passed through literally.
        """
        argv = self.build_argv(files)
        return subprocess.list2cmdline(argv) if windows else shlex.join(argv)

    def parse_output(self, stdout: str, stderr: str) -> list[Diagnostic]:
        """Parse CLI output into diagnostics. Override in subclasses."""
        return []


def _severity_from_string(severity: str) -> Literal["error", "warning", "info", "hint"]:
    """Convert severity string to Diagnostic severity."""
    severity = severity.lower()
    match severity:
        case "error" | "err" | "blocker" | "critical" | "major":
            return "error"
        case "warning" | "warn" | "minor":
            return "warning"
        case "info" | "information":
            return "info"
        case "hint" | "note":
            return "hint"
        case _:
            return "warning"


# -----------------------------------------------------------------------------
# Python Servers
# -----------------------------------------------------------------------------


@dataclass
class TyServer(DiagnosticServer):
    """Ty (Astral) type checker - Rust-based, very fast."""

    def parse_output(self, stdout: str, stderr: str) -> list[Diagnostic]:
        """Parse ty GitLab JSON output."""
        diagnostics: list[Diagnostic] = []
        try:
            for item in anyenv.load_json(stdout):
                location = item.get("location", {})
                positions = location.get("positions", {})
                begin = positions.get("begin", {})
                end = positions.get("end", {})

                diagnostics.append(
                    Diagnostic(
                        file=location.get("path", ""),
                        line=begin.get("line", 1),
                        column=begin.get("column", 1),
                        end_line=end.get("line", begin.get("line", 1)),
                        end_column=end.get("column", begin.get("column", 1)),
                        severity=_severity_from_string(item.get("severity", "major")),
                        message=item.get("description", ""),
                        code=item.get("check_name"),
                        source=self.id,
                    )
                )
        except anyenv.JsonLoadError:
            pass
        return diagnostics


@dataclass
class PyrightServer(DiagnosticServer):
    """Pyright type checker - Node.js based."""

    def parse_output(self, stdout: str, stderr: str) -> list[Diagnostic]:
        """Parse pyright JSON output."""
        diagnostics: list[Diagnostic] = []
        try:
            # Find JSON object in output (may have warnings before it)
            json_start = stdout.find("{")
            if json_start == -1:
                return diagnostics
            data = anyenv.load_json(stdout[json_start:])

            for diag in data.get("generalDiagnostics", []):
                range_info = diag.get("range", {})
                start = range_info.get("start", {})
                end = range_info.get("end", {})

                diagnostics.append(
                    Diagnostic(
                        file=diag.get("file", ""),
                        line=start.get("line", 0) + 1,  # pyright uses 0-indexed
                        column=start.get("character", 0) + 1,
                        end_line=end.get("line", start.get("line", 0)) + 1,
                        end_column=end.get("character", start.get("character", 0)) + 1,
                        severity=_severity_from_string(diag.get("severity", "error")),
                        message=diag.get("message", ""),
                        code=diag.get("rule"),
                        source=self.id,
                    )
                )
        except anyenv.JsonLoadError:
            pass
        return diagnostics


@dataclass
class MypyServer(DiagnosticServer):
    """Mypy type checker - Python based."""

    def parse_output(self, stdout: str, stderr: str) -> list[Diagnostic]:
        """Parse mypy JSON output (one JSON object per line)."""
        diagnostics: list[Diagnostic] = []
        for raw_line in stdout.strip().splitlines():
            line = raw_line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                data = anyenv.load_json(line)
                diagnostics.append(
                    Diagnostic(
                        file=data.get("file", ""),
                        line=data.get("line", 1),
                        column=data.get("column", 1),
                        severity=_severity_from_string(data.get("severity", "error")),
                        message=data.get("message", ""),
                        code=data.get("code"),
                        source=self.id,
                    )
                )
            except anyenv.JsonLoadError:
                continue
        return diagnostics


@dataclass
class ZubanServer(DiagnosticServer):
    """Zuban type checker - mypy-compatible output."""

    def parse_output(self, stdout: str, stderr: str) -> list[Diagnostic]:
        """Parse zuban mypy-compatible text output."""
        diagnostics: list[Diagnostic] = []
        # Pattern: path:line:col: severity: message  [code]
        pattern = re.compile(
            r"^(.+?):(\d+):(\d+): (error|warning|note): (.+?)(?:\s+\[([^\]]+)\])?$"
        )

        for raw_line in (stdout or stderr).strip().splitlines():
            line = raw_line.strip()
            if match := pattern.match(line):
                file_path, line_no, col, severity, message, code = match.groups()
                diagnostics.append(
                    Diagnostic(
                        file=file_path,
                        line=int(line_no),
                        column=int(col),
                        severity=_severity_from_string(severity),
                        message=message.strip(),
                        code=code,
                        source=self.id,
                    )
                )
        return diagnostics


@dataclass
class PyreflyServer(DiagnosticServer):
    """Pyrefly (Meta) type checker."""

    def parse_output(self, stdout: str, stderr: str) -> list[Diagnostic]:
        """Parse pyrefly JSON output."""
        diagnostics: list[Diagnostic] = []
        try:
            json_start = stdout.find("{")
            json_end = stdout.rfind("}") + 1
            if json_start == -1 or json_end == 0:
                return diagnostics

            data = anyenv.load_json(stdout[json_start:json_end])
            diagnostics.extend(
                Diagnostic(
                    file=error.get("path", ""),
                    line=error.get("line", 1),
                    column=error.get("column", 1),
                    end_line=error.get("stop_line", error.get("line", 1)),
                    end_column=error.get("stop_column", error.get("column", 1)),
                    severity=_severity_from_string(error.get("severity", "error")),
                    message=error.get("description", ""),
                    code=error.get("name"),
                    source=self.id,
                )
                for error in data.get("errors", [])
            )
        except anyenv.JsonLoadError:
            pass
        return diagnostics


# -----------------------------------------------------------------------------
# JavaScript/TypeScript Servers
# -----------------------------------------------------------------------------


@dataclass
class OxlintServer(DiagnosticServer):
    """Oxlint linter - Rust-based, very fast."""

    def parse_output(self, stdout: str, stderr: str) -> list[Diagnostic]:
        """Parse oxlint JSON output."""
        diagnostics: list[Diagnostic] = []
        try:
            data = anyenv.load_json(stdout)
            for diag in data.get("diagnostics", []):
                if labels := diag.get("labels", []):
                    span = labels[0].get("span", {})
                    line = span.get("line", 1)
                    column = span.get("column", 1)
                else:
                    line, column = 1, 1
                obj = Diagnostic(
                    file=diag.get("filename", ""),
                    line=line,
                    column=column,
                    severity=_severity_from_string(diag.get("severity", "warning")),
                    message=diag.get("message", ""),
                    code=diag.get("code"),
                    source=self.id,
                )
                diagnostics.append(obj)
        except anyenv.JsonLoadError:
            pass
        return diagnostics


@dataclass
class BiomeServer(DiagnosticServer):
    """Biome linter/formatter - Rust-based."""

    def parse_output(self, stdout: str, stderr: str) -> list[Diagnostic]:
        """Parse biome JSON output."""
        diagnostics: list[Diagnostic] = []
        try:
            json_start = stdout.find("{")
            if json_start == -1:
                return diagnostics

            data = anyenv.load_json(stdout[json_start:])
            for diag in data.get("diagnostics", []):
                location = diag.get("location", {})
                span = location.get("span", [0, 0])
                path_info = location.get("path", {})
                file_path = path_info.get("file", "") if isinstance(path_info, dict) else ""
                obj = Diagnostic(
                    file=file_path,
                    line=1,  # Biome uses byte offsets
                    column=span[0] if span else 1,
                    severity=_severity_from_string(diag.get("severity", "error")),
                    message=diag.get("description", ""),
                    code=diag.get("category"),
                    source=self.id,
                )
                diagnostics.append(obj)
        except anyenv.JsonLoadError:
            pass
        return diagnostics


# =============================================================================
# Server Registry
# =============================================================================

# Python servers
TY = TyServer(
    id="ty",
    extensions=[".py", ".pyi"],
    cli=CLIDiagnosticConfig(
        command="ty",
        args=["check", "--output-format", "gitlab", "{files}"],
        output_format="json",
    ),
    rust_based=True,
    priority=10,
)

PYRIGHT = PyrightServer(
    id="pyright",
    extensions=[".py", ".pyi"],
    cli=CLIDiagnosticConfig(
        command="pyright",
        args=["--outputjson", "{files}"],
        output_format="json",
    ),
    rust_based=False,
    priority=20,
)

BASEDPYRIGHT = PyrightServer(
    id="basedpyright",
    extensions=[".py", ".pyi"],
    cli=CLIDiagnosticConfig(
        command="basedpyright",
        args=["--outputjson", "{files}"],
        output_format="json",
    ),
    rust_based=False,
    priority=25,
)

MYPY = MypyServer(
    id="mypy",
    extensions=[".py", ".pyi"],
    cli=CLIDiagnosticConfig(
        command="mypy",
        args=["--output", "json", "{files}"],
        output_format="json",
    ),
    rust_based=False,
    priority=30,
)

ZUBAN = ZubanServer(
    id="zuban",
    extensions=[".py", ".pyi"],
    cli=CLIDiagnosticConfig(
        command="zuban",
        args=["check", "--show-column-numbers", "--show-error-codes", "{files}"],
        output_format="text",
    ),
    rust_based=False,
    priority=35,
)

PYREFLY = PyreflyServer(
    id="pyrefly",
    extensions=[".py", ".pyi"],
    cli=CLIDiagnosticConfig(
        command="pyrefly",
        args=["check", "--output-format", "json", "{files}"],
        output_format="json",
    ),
    rust_based=False,
    priority=40,
)

# JavaScript/TypeScript servers
OXLINT = OxlintServer(
    id="oxlint",
    extensions=[".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue"],
    cli=CLIDiagnosticConfig(
        command="oxlint",
        args=["--format", "json", "{files}"],
        output_format="json",
    ),
    rust_based=True,
    priority=10,
)

BIOME = BiomeServer(
    id="biome",
    extensions=[".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".json", ".jsonc"],
    cli=CLIDiagnosticConfig(
        command="biome",
        args=["lint", "--reporter=json", "{files}"],
        output_format="json",
    ),
    rust_based=True,
    priority=15,
)

# All available servers, ordered by priority within each language
ALL_SERVERS: list[DiagnosticServer] = [
    # Python (Rust-based first)
    TY,
    # PYRIGHT,
    # BASEDPYRIGHT,
    # MYPY,
    # ZUBAN,  # Disabled: times out on large files (>1000 lines)
    PYREFLY,
    # JavaScript/TypeScript (Rust-based)
    OXLINT,
    BIOME,
]

# Quick lookup by ID
SERVERS_BY_ID: dict[str, DiagnosticServer] = {s.id: s for s in ALL_SERVERS}

# Rust-based server IDs for quick filtering
RUST_BASED_IDS: set[str] = {s.id for s in ALL_SERVERS if s.rust_based}


# =============================================================================
# Progress Callback Protocol
# =============================================================================


class ProgressCallback(Protocol):
    """Protocol for progress notifications during diagnostic runs."""

    async def __call__(
        self,
        message: str,
        *,
        server_id: str | None = None,
        command: str | None = None,
        status: Literal["starting", "running", "completed", "failed"] = "running",
    ) -> None:
        """Report progress during diagnostic execution."""
        ...


# =============================================================================
# Diagnostics Manager
# =============================================================================


class DiagnosticsManager:
    """Orchestrates diagnostic tool execution with rich configurability.

    Features:
    - Server selection via config (preferred, excluded, rust-only)
    - Availability caching (checks `which` once per server)
    - Parallel execution option
    - Progress callbacks with command details
    - Rich result metadata
    """

    def __init__(
        self,
        env: ExecutionEnvironment,
        config: DiagnosticsConfig | None = None,
    ) -> None:
        """Initialize diagnostics manager.

        Args:
            env: Execution environment for running diagnostic commands.
            config: Configuration for server selection and execution.
        """
        self._env = env
        self._config = config or DiagnosticsConfig()
        self._availability_cache: dict[str, bool] = {}

    @property
    def config(self) -> DiagnosticsConfig:
        """Get current configuration."""
        return self._config

    @config.setter
    def config(self, value: DiagnosticsConfig) -> None:
        """Set configuration."""
        self._config = value

    def get_servers_for_extension(self, extension: str) -> list[DiagnosticServer]:
        """Get all servers that can handle a file extension, filtered by config."""
        ext = extension if extension.startswith(".") else f".{extension}"

        # Start with all servers that handle this extension
        servers = [s for s in ALL_SERVERS if s.can_handle(ext)]

        # Apply rust_only filter
        if self._config.rust_only:
            servers = [s for s in servers if s.rust_based]

        # Apply preferred_servers filter (if set, only these servers)
        if self._config.preferred_servers:
            preferred = set(self._config.preferred_servers)
            servers = [s for s in servers if s.id in preferred]

        # Apply excluded_servers filter
        if self._config.excluded_servers:
            excluded = set(self._config.excluded_servers)
            servers = [s for s in servers if s.id not in excluded]

        # Sort by priority
        servers.sort(key=lambda s: s.priority)

        # Apply max_servers_per_language limit
        if self._config.max_servers_per_language > 0:
            servers = servers[: self._config.max_servers_per_language]

        return servers

    def get_servers_for_file(self, path: str) -> list[DiagnosticServer]:
        """Get servers for a file path."""
        import posixpath

        ext = posixpath.splitext(path)[1]
        return self.get_servers_for_extension(ext)

    def _is_windows(self) -> bool:
        """Whether commands run in a Windows shell (cmd.exe quoting rules)."""
        try:
            return self._env.os_type == "Windows"
        except RuntimeError:  # environment not entered yet
            return False

    async def check_availability(self, server: DiagnosticServer) -> bool:
        """Check if a server's command is available (cached)."""
        if server.id in self._availability_cache:
            return self._availability_cache[server.id]

        # Use 'which' or 'where' depending on OS
        if self._is_windows():
            cmd = subprocess.list2cmdline(["where", server.cli.command])
        else:
            cmd = shlex.join(["which", server.cli.command])

        result = await self._env.execute_command(cmd)
        available = result.exit_code == 0 and bool(result.stdout and result.stdout.strip())

        self._availability_cache[server.id] = available
        logger.debug("Server %s availability: %s", server.id, available)
        return available

    async def _run_server(
        self,
        server: DiagnosticServer,
        files: list[str],
        progress: ProgressCallback | None = None,
    ) -> DiagnosticRunResult:
        """Run a single diagnostic server."""
        command = server.build_command(files, windows=self._is_windows())

        if progress:
            await progress(
                f"Running {server.id}...",
                server_id=server.id,
                command=command,
                status="starting",
            )

        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self._env.execute_command(command),
                timeout=self._config.timeout if self._config.timeout > 0 else None,
            )
            duration = time.perf_counter() - start

            diagnostics = server.parse_output(result.stdout or "", result.stderr or "")

            if progress:
                await progress(
                    f"{server.id}: {len(diagnostics)} issues",
                    server_id=server.id,
                    command=command,
                    status="completed",
                )

            return DiagnosticRunResult(
                server_id=server.id,
                command=command,
                diagnostics=diagnostics,
                duration=duration,
                success=True,
            )

        except TimeoutError:
            duration = time.perf_counter() - start
            error_msg = f"Timeout after {self._config.timeout}s"
            if progress:
                await progress(
                    f"{server.id}: {error_msg}",
                    server_id=server.id,
                    command=command,
                    status="failed",
                )
            return DiagnosticRunResult(
                server_id=server.id,
                command=command,
                diagnostics=[],
                duration=duration,
                success=False,
                error=error_msg,
            )

        except Exception as e:  # noqa: BLE001
            duration = time.perf_counter() - start
            error_msg = f"{type(e).__name__}: {e}"
            if progress:
                await progress(
                    f"{server.id}: {error_msg}",
                    server_id=server.id,
                    command=command,
                    status="failed",
                )
            return DiagnosticRunResult(
                server_id=server.id,
                command=command,
                diagnostics=[],
                duration=duration,
                success=False,
                error=error_msg,
            )

    async def run_for_file(
        self,
        path: str,
        progress: ProgressCallback | None = None,
    ) -> DiagnosticsResult:
        """Run all applicable diagnostics on a single file.

        Args:
            path: File path to check.
            progress: Optional callback for progress notifications.

        Returns:
            DiagnosticsResult with all diagnostics and run metadata.
        """
        return await self.run_for_files([path], progress=progress)

    async def run_for_files(
        self,
        files: list[str],
        progress: ProgressCallback | None = None,
    ) -> DiagnosticsResult:
        """Run diagnostics on multiple files.

        Args:
            files: File paths to check.
            progress: Optional callback for progress notifications.

        Returns:
            DiagnosticsResult with all diagnostics and run metadata.
        """
        if not files:
            return DiagnosticsResult(diagnostics=[], runs=[], total_duration=0.0)

        start = time.perf_counter()

        # Collect all applicable servers across all files
        import posixpath

        extensions = {posixpath.splitext(f)[1] for f in files}
        servers_to_run: list[DiagnosticServer] = []
        seen_ids: set[str] = set()

        for ext in extensions:
            for server in self.get_servers_for_extension(ext):
                if server.id not in seen_ids:
                    seen_ids.add(server.id)
                    servers_to_run.append(server)

        # Check availability (can't use list comprehension due to await)
        available_servers: list[DiagnosticServer] = []
        for server in servers_to_run:
            if await self.check_availability(server):
                available_servers.append(server)  # noqa: PERF401

        if not available_servers:
            return DiagnosticsResult(
                diagnostics=[],
                runs=[],
                total_duration=time.perf_counter() - start,
            )

        # Run servers (parallel or sequential)
        if self._config.parallel and len(available_servers) > 1:
            tasks = [self._run_server(s, files, progress) for s in available_servers]
            runs = await asyncio.gather(*tasks)
        else:
            runs = []
            for server in available_servers:
                run_result = await self._run_server(server, files, progress)
                runs.append(run_result)

        # Combine diagnostics
        all_diagnostics: list[Diagnostic] = []
        for run in runs:
            all_diagnostics.extend(run.diagnostics)

        total_duration = time.perf_counter() - start

        return DiagnosticsResult(
            diagnostics=all_diagnostics,
            runs=list(runs),
            total_duration=total_duration,
        )


# =============================================================================
# Formatting Helpers
# =============================================================================


def format_diagnostics_table(diagnostics: list[Diagnostic]) -> str:
    """Format diagnostics as a Markdown table.

    Args:
        diagnostics: List of diagnostics to format.

    Returns:
        Markdown table string.
    """
    if not diagnostics:
        return "No issues found."

    lines: list[str] = [
        "| Severity | Location | Code | Source | Description |",
        "|----------|----------|------|--------|-------------|",
    ]
    for d in diagnostics:
        loc = f"{d.file}:{d.line}:{d.column}"
        code = d.code or ""
        # Escape pipe characters and newlines in message
        msg = d.message.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {d.severity.upper()} | {loc} | {code} | {d.source} | {msg} |")

    return "\n".join(lines)


def format_diagnostics_compact(diagnostics: list[Diagnostic]) -> str:
    """Format diagnostics in a compact single-line-per-issue format.

    Args:
        diagnostics: List of diagnostics to format.

    Returns:
        Compact formatted string.
    """
    if not diagnostics:
        return "No issues found."

    lines: list[str] = []
    for d in diagnostics:
        code_part = f"[{d.code}] " if d.code else ""
        msg = d.message.replace("\n", " ")
        lines.append(f"{d.file}:{d.line}:{d.column}: {d.severity}: {code_part}{msg} ({d.source})")

    return "\n".join(lines)


def format_run_summary(result: DiagnosticsResult) -> str:
    """Format a summary of the diagnostic run.

    Args:
        result: The diagnostics result to summarize.

    Returns:
        Summary string.
    """
    parts = [f"Ran {len(result.runs)} tool(s) in {result.total_duration:.2f}s"]

    if result.diagnostics:
        parts.append(f"Found {len(result.diagnostics)} issues")
        if result.error_count:
            parts.append(f"({result.error_count} errors, {result.warning_count} warnings)")
    else:
        parts.append("No issues found")

    # Add per-server timing
    if result.runs:
        timings = ", ".join(f"{r.server_id}: {r.duration:.2f}s" for r in result.runs)
        parts.append(f"[{timings}]")

    return " | ".join(parts)
