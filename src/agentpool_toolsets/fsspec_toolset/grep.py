"""Grep functionality for filesystem search operations."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

import anyenv

from agentpool.log import get_logger
from agentpool_toolsets.fsspec_toolset.helpers import DEFAULT_MAX_SIZE


if TYPE_CHECKING:
    from exxec.base import ExecutionEnvironment
    from fsspec.asyn import AsyncFileSystem


logger = get_logger(__name__)


GrepBackend = Literal["fsspec", "ripgrep", "grep"]


# Default patterns to exclude from grep searches
DEFAULT_EXCLUDE_PATTERNS = [
    ".venv/",
    "venv/",
    ".env/",
    "env/",
    "node_modules/",
    ".git/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".tox/",
    ".nox/",
    ".coverage/",
    "htmlcov/",
    "dist/",
    "build/",
    ".idea/",
    ".vscode/",
    "*.egg-info",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".DS_Store",
    "Thumbs.db",
]


async def detect_grep_backend(env: ExecutionEnvironment) -> GrepBackend:
    """Detect available grep backend in the execution environment.

    Args:
        env: ExecutionEnvironment to check for available tools

    Returns:
        Best available backend (ripgrep > GNU grep > Python)
    """
    from anyenv.os_commands import get_os_command_provider

    provider = get_os_command_provider(env.os_type)
    which_cmd = provider.get_command("which")

    # Try ripgrep first
    cmd = which_cmd.create_command("rg")
    result = await env.execute_command(cmd)
    if which_cmd.parse_command(result.stdout or "", result.exit_code or 0):
        return "ripgrep"

    # Try GNU grep
    cmd = which_cmd.create_command("grep")
    result = await env.execute_command(cmd)
    if which_cmd.parse_command(result.stdout or "", result.exit_code or 0):
        return "grep"

    return "fsspec"


def _is_path_inside_ignored_dir(path: str) -> bool:
    """Check if path is explicitly inside a commonly ignored directory.

    Args:
        path: The search path

    Returns:
        True if path is inside a directory that would typically be gitignored
    """
    # Common ignored directory patterns (without trailing slash)
    ignored_dirs = [
        ".venv",
        "venv",
        ".env",
        "env",
        "node_modules",
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        ".nox",
    ]
    for ignored in ignored_dirs:
        if (
            path.startswith((f"{ignored}/", f"./{ignored}/"))
            or f"/{ignored}/" in path
            or path == ignored
        ):
            return True
    return False


def _filter_exclude_patterns(path: str, exclude_patterns: list[str]) -> list[str]:
    """Filter out exclude patterns that the search path is explicitly inside.

    If user explicitly searches inside an excluded directory (e.g., .venv/),
    don't exclude that directory from results.

    Args:
        path: The search path
        exclude_patterns: List of patterns to potentially exclude

    Returns:
        Filtered list of exclude patterns
    """
    filtered = []
    for pattern in exclude_patterns:
        # Normalize pattern (remove trailing slash for comparison)
        pattern_normalized = pattern.rstrip("/")
        # Check if the search path starts with or contains this excluded dir
        # e.g., path=".venv/lib/python" should not exclude ".venv/"
        if not (
            path.startswith((pattern_normalized, f"./{pattern_normalized}"))
            or f"/{pattern_normalized}/" in path
        ):
            filtered.append(pattern)
    return filtered


async def grep_with_subprocess(
    env: ExecutionEnvironment,
    pattern: str,
    path: str,
    *,
    backend: GrepBackend | None = None,
    case_sensitive: bool = False,
    max_matches: int = 100,
    max_output_bytes: int = DEFAULT_MAX_SIZE,
    exclude_patterns: list[str] | None = None,
    use_gitignore: bool = True,
    context_lines: int = 0,
    file_pattern: str = "**/*",
    timeout: int = 60,
) -> dict[str, Any]:
    """Execute grep using ExecutionEnvironment (ripgrep or GNU grep).

    Args:
        env: ExecutionEnvironment to run the command in
        pattern: Regex pattern to search for
        path: Directory or file path to search
        backend: Grep backend to use (auto-detected if None)
        case_sensitive: Whether search is case-sensitive
        max_matches: Maximum number of matches per file
        max_output_bytes: Maximum total output bytes
        exclude_patterns: Patterns to exclude from search
        use_gitignore: Whether to respect .gitignore files
        context_lines: Number of context lines before/after match
        file_pattern: Glob pattern to filter files (e.g. "**/*.py")
        timeout: Command timeout in seconds

    Returns:
        Dictionary with matches, match_count, and was_truncated flag
    """
    import shlex

    if backend is None:
        backend = await detect_grep_backend(env)

    if backend == "fsspec":
        raise ValueError("Subprocess grep requested but no grep/ripgrep found")

    base_exclude = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS
    # Filter out patterns that the user is explicitly searching inside
    exclude = _filter_exclude_patterns(path, base_exclude)
    # Disable gitignore if explicitly searching inside an ignored directory
    # (e.g., searching .venv/ when .venv is in .gitignore)
    effective_use_gitignore = use_gitignore and not _is_path_inside_ignored_dir(path)

    if backend == "ripgrep":
        cmd_list = _build_ripgrep_command(
            pattern,
            path,
            case_sensitive,
            max_matches,
            exclude,
            effective_use_gitignore,
            context_lines,
            file_pattern,
        )
    else:
        include = _gnu_include_glob(file_pattern)
        if include is False:
            return {
                "error": f"grep cannot filter by {file_pattern!r} (only file name globs)",
                "matches": "",
                "match_count": 0,
                "was_truncated": False,
            }
        cmd_list = _build_gnu_grep_command(
            pattern, path, case_sensitive, max_matches, exclude, context_lines, include
        )
    # Convert list to shell command string
    command = " ".join(shlex.quote(arg) for arg in cmd_list)
    try:
        result = await env.execute_command(command, timeout=timeout)
        # Exit code 0 = matches found, 1 = no matches, 2+ = error
        if result.exit_code not in {0, 1, None}:
            error_msg = result.stderr or f"Process exited with code {result.exit_code}"
            return {
                "error": f"grep error: {error_msg}",
                "matches": "",
                "match_count": 0,
                "was_truncated": False,
            }

        # Use appropriate parser based on backend
        if backend == "ripgrep":
            return _parse_ripgrep_json_output(result.stdout or "", max_output_bytes)
        return _parse_gnu_grep_output(result.stdout or "", max_output_bytes)

    except Exception as e:
        logger.exception("Error running grep command")
        return {
            "error": f"Error running grep: {e}",
            "matches": "",
            "match_count": 0,
            "was_truncated": False,
        }


def _build_ripgrep_command(
    pattern: str,
    path: str,
    case_sensitive: bool,
    max_matches: int,
    exclude_patterns: list[str],
    use_gitignore: bool,
    context_lines: int = 0,
    file_pattern: str = "**/*",
) -> list[str]:
    """Build ripgrep command with JSON output."""
    cmd = [
        "rg",
        "--json",  # Use JSON output for robust parsing
        "--no-binary",
        "--max-count",
        str(max_matches + 1),  # Request one extra to detect truncation
    ]

    if not case_sensitive:
        cmd.append("--smart-case")

    if not use_gitignore:
        cmd.append("--no-ignore")

    # Add context lines if requested
    if context_lines > 0:
        cmd.extend(["--context", str(context_lines)])

    # The include glob comes first: later globs take precedence, so excludes still win
    if file_pattern not in _MATCH_ALL_GLOBS:
        cmd.extend(["--glob", file_pattern])
    for pattern_exclude in exclude_patterns:
        cmd.extend(["--glob", f"!{pattern_exclude}"])

    cmd.extend(["-e", pattern, path])

    return cmd


_MATCH_ALL_GLOBS = frozenset({"*", "**", "**/*"})


def _gnu_include_glob(file_pattern: str) -> str | Literal[False] | None:
    """Translate a glob into a GNU grep `--include` glob (matched against file names).

    Returns None for "all files" and False if the glob filters by directory,
    which `--include` cannot express.
    """
    if file_pattern in _MATCH_ALL_GLOBS:
        return None
    name_glob = file_pattern.removeprefix("**/")
    return False if "/" in name_glob or "**" in name_glob else name_glob


def _build_gnu_grep_command(
    pattern: str,
    path: str,
    case_sensitive: bool,
    max_matches: int,
    exclude_patterns: list[str],
    context_lines: int = 0,
    include: str | None = None,
) -> list[str]:
    """Build GNU grep command."""
    cmd = [
        "grep",
        "-r",
        "-n",
        "--null",  # NUL after the file name: separates it from `:` (match) / `-` (context)
        "-I",  # Skip binary files
        "-E",  # Extended regex
        f"--max-count={max_matches + 1}",
    ]
    if include:
        cmd.append(f"--include={include}")

    if not case_sensitive and pattern.islower():
        cmd.append("-i")

    # Add context lines if requested
    if context_lines > 0:
        cmd.append(f"--context={context_lines}")

    for pattern_exclude in exclude_patterns:
        if pattern_exclude.endswith("/"):
            dir_pattern = pattern_exclude.rstrip("/")
            cmd.append(f"--exclude-dir={dir_pattern}")
        else:
            cmd.append(f"--exclude={pattern_exclude}")

    cmd.extend(["-e", pattern, path])

    return cmd


def _parse_ripgrep_json_output(stdout: str, max_output_bytes: int) -> dict[str, Any]:
    """Parse ripgrep JSON output and format as markdown table.

    Ripgrep --json outputs one JSON object per line with type field:
    - "match": actual match with path, line_number, lines.text
    - "context": context lines around matches
    - "begin"/"end": file boundaries
    - "summary": final statistics
    """
    if not stdout:
        return {"matches": "", "match_count": 0, "was_truncated": False}

    matches: list[dict[str, Any]] = []
    total_bytes = 0
    was_truncated = False
    match_count = 0  # Track actual matches (not context lines)

    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            data = anyenv.load_json(line, return_type=dict)
            entry_type = data.get("type")

            if entry_type in ("match", "context"):
                match_data = data.get("data", {})
                path = match_data.get("path", {}).get("text", "")
                line_num = match_data.get("line_number", 0)
                content = match_data.get("lines", {}).get("text", "").rstrip("\n")

                match_entry = {
                    "path": path,
                    "line": line_num,
                    "content": content,
                    "is_match": entry_type == "match",
                }
                entry_size = len(path) + len(str(line_num)) + len(content) + 10

                if total_bytes + entry_size > max_output_bytes:
                    was_truncated = True
                    break

                matches.append(match_entry)
                total_bytes += entry_size

                if entry_type == "match":
                    match_count += 1
        except anyenv.JsonLoadError:
            continue

    # Format as markdown table
    if not matches:
        return {"matches": "", "match_count": 0, "was_truncated": False}

    table_lines = ["| File | Line | Content |", "|------|------|---------|"]
    for m in matches:
        # Escape pipe characters in content for markdown table
        content = m["content"].replace("|", "\\|")
        # Truncate long lines for readability
        if len(content) > 100:  # noqa: PLR2004
            content = content[:97] + "..."
        # Mark match lines with indicator if there are context lines
        prefix = "→ " if m.get("is_match") and match_count < len(matches) else ""
        table_lines.append(f"| {m['path']} | {m['line']} | {prefix}{content} |")

    return {
        "matches": "\n".join(table_lines),
        "match_count": match_count,
        "was_truncated": was_truncated,
    }


def _parse_gnu_grep_output(stdout: str, max_output_bytes: int) -> dict[str, Any]:
    """Parse GNU grep output and format as markdown table.

    GNU grep output format (with `--null`): `file<NUL>linenum:content` for matches and
    `file<NUL>linenum-content` for context lines.
    """
    if not stdout:
        return {"matches": "", "match_count": 0, "was_truncated": False}

    matches: list[dict[str, Any]] = []
    total_bytes = 0
    was_truncated = False
    line_pattern = re.compile(r"^(.*?)\0(\d+)([:-])(.*)$")

    for line in stdout.splitlines():
        if line.strip() and line != "--" and (match := line_pattern.match(line)):
            path, line_num, sep, content = match.groups()
            entry_size = len(path) + len(line_num) + len(content) + 10
            if total_bytes + entry_size > max_output_bytes:
                was_truncated = True
                break
            matches.append({
                "path": path,
                "line": int(line_num),
                "content": content,
                "is_match": sep == ":",
            })
            total_bytes += entry_size

    return _format_matches(matches, was_truncated)


def _format_matches(matches: list[dict[str, Any]], was_truncated: bool) -> dict[str, Any]:
    """Format match (and context) entries as a markdown table."""
    match_count = sum(1 for m in matches if m["is_match"])
    if not matches:
        return {"matches": "", "match_count": 0, "was_truncated": was_truncated}

    has_context = match_count < len(matches)
    table_lines = ["| File | Line | Content |", "|------|------|---------|"]
    for m in matches:
        content = m["content"].replace("|", "\\|")
        if len(content) > 100:  # noqa: PLR2004
            content = content[:97] + "..."
        # Mark match lines with indicator if there are context lines
        prefix = "→ " if has_context and m["is_match"] else ""
        table_lines.append(f"| {m['path']} | {m['line']} | {prefix}{content} |")

    return {
        "matches": "\n".join(table_lines),
        "match_count": match_count,
        "was_truncated": was_truncated,
    }


async def grep_with_fsspec(
    fs: AsyncFileSystem,
    pattern: str,
    path: str,
    *,
    file_pattern: str = "**/*",
    case_sensitive: bool = False,
    max_matches: int = 100,
    max_output_bytes: int = DEFAULT_MAX_SIZE,
    context_lines: int = 0,
) -> dict[str, Any]:
    """Execute grep using fsspec filesystem (Python implementation).

    Args:
        fs: FSSpec filesystem instance
        pattern: Regex pattern to search for
        path: Base directory to search in
        file_pattern: Glob pattern to filter files
        case_sensitive: Whether search is case-sensitive
        max_matches: Maximum total matches across all files
        max_output_bytes: Maximum total output bytes
        context_lines: Number of context lines before/after match

    Returns:
        Dictionary with matches as markdown table
    """
    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)
    except re.error as e:
        return {"error": f"Invalid regex pattern: {e}"}

    glob_path = f"{path.rstrip('/')}/{file_pattern}"
    matches: list[dict[str, Any]] = []
    match_count = 0
    total_bytes = 0
    was_truncated = False
    try:
        file_paths = await fs._glob(glob_path)
        for file_path in file_paths:
            if was_truncated or match_count >= max_matches or total_bytes >= max_output_bytes:
                was_truncated = True
                break

            # Skip directories
            if await fs._isdir(file_path):
                continue

            try:
                file_content = await fs._cat(file_path)
                # Skip binary files
                if b"\x00" in file_content[:8192]:
                    continue
                text = file_content.decode("utf-8", errors="replace")
                lines = text.splitlines()

                last_emitted = 0  # Last line number (1-based) added for this file
                for idx, line in enumerate(lines):
                    if match_count >= max_matches or total_bytes >= max_output_bytes:
                        was_truncated = True
                        break
                    if not regex.search(line):
                        continue
                    start = max(idx - context_lines, last_emitted)
                    end = min(idx + context_lines + 1, len(lines))
                    for i in range(start, end):
                        content = lines[i].rstrip()
                        entry_size = len(file_path) + len(str(i + 1)) + len(content) + 10
                        if total_bytes + entry_size > max_output_bytes:
                            was_truncated = True
                            break
                        is_match = i == idx or (i > idx and bool(regex.search(lines[i])))
                        matches.append({
                            "path": file_path,
                            "line": i + 1,
                            "content": content,
                            "is_match": is_match,
                        })
                        total_bytes += entry_size
                        last_emitted = i + 1
                    if was_truncated:
                        break
                    match_count += 1

            except Exception as e:  # noqa: BLE001
                logger.debug("Error reading file during grep", file=file_path, error=str(e))
                continue

    except Exception as e:
        logger.exception("Error in fsspec grep")
        return {"error": f"Grep failed: {e}"}

    return _format_matches(matches, was_truncated)
