"""Sophisticated file editing tool with multiple replacement strategies.

This module provides a file editing interface using sublime_search's Rust-based
replacement engine with multiple fallback strategies.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentpool.tools.base import FunctionTool
from agentpool.utils.diffs import compute_unified_diff, count_changed_lines


if TYPE_CHECKING:
    from agentpool.agents import AgentContext


async def edit_file_tool(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    context: AgentContext | None = None,
) -> dict[str, Any]:
    """Perform exact string replacements in files using sophisticated matching strategies.

    Uses sublime_search's Rust implementation with 8 fallback strategies:
    1. Simple - Direct string matching
    2. Line-trimmed - Line-by-line matching with trimmed whitespace
    3. Block-anchor - Multi-line matching using first/last line anchors
    4. Whitespace-normalized - Normalized whitespace matching
    5. Indentation-flexible - Matching with flexible indentation
    6. Escape-normalized - Handle escape sequence differences
    7. Trimmed-boundary - Match with trimmed boundaries
    8. Context-aware - Use anchor lines for context matching

    Args:
        file_path: Path to the file to modify
        old_string: Text to replace
        new_string: Text to replace it with
        replace_all: Whether to replace all occurrences
        context: Agent execution context

    Returns:
        Dict with operation results including diff and any errors
    """
    from sublime_search import replace_content, trim_diff

    if old_string == new_string:
        msg = "old_string and new_string must be different"
        raise ValueError(msg)

    # Resolve file path
    path = Path(file_path)
    if not path.is_absolute():
        path = Path.cwd() / path

    # Validate file exists and is a file
    if not path.exists():
        msg = f"File not found: {path}"
        raise FileNotFoundError(msg)

    if not path.is_file():
        msg = f"Path is a directory, not a file: {path}"
        raise ValueError(msg)

    # Read current content
    try:
        original_content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        original_content = path.read_text(encoding="latin-1")

    # Handle empty file case
    if old_string == "" and original_content == "":
        new_content = new_string
    else:
        result = replace_content(original_content, old_string, new_string, replace_all)
        new_content = result.content

    # Generate diff
    diff_text = compute_unified_diff(
        original_content, new_content, fromfile=str(path), tofile=str(path)
    )
    # Write new content
    try:
        path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        raise RuntimeError(f"Failed to write file: {e}") from e

    return {
        "success": True,
        "file_path": str(path),
        "diff": trim_diff(diff_text) if diff_text else "",
        "message": f"Successfully edited {path.name}",
        "lines_changed": count_changed_lines(diff_text),
    }


# Create the tool instance
edit_tool = FunctionTool.from_callable(edit_file_tool, name_override="edit_file")
