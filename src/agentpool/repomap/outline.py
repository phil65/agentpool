"""Single-file outline generation - centralized entry point."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

from agentpool.repomap.languages import is_language_supported
from agentpool.repomap.tags import get_tags_from_content


async def generate_file_outline(
    path: str | Path,
    fs: AsyncFileSystem | None = None,
    content: str | None = None,
    max_tokens: int = 2048,
) -> str | None:
    """Generate structure outline for a file - universal entry point.

    This is the centralized function that all tools and context generation
    should use for creating file outlines.

    Args:
        path: File path (for language detection and display)
        fs: Optional filesystem (if not provided and content is None, reads from path)
        content: Optional pre-loaded content (avoids re-reading file)
        max_tokens: Maximum tokens for output (approximate)

    Returns:
        Outline string with structure map, or None if language not supported
    """
    path_str = str(path)

    # Check language support first
    if not is_language_supported(Path(path_str).name):
        return None

    # Get content if not provided
    if content is None:
        if fs is not None:
            # Read from filesystem
            try:
                data = await fs._cat_file(path_str)
                content = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data
            except Exception:  # noqa: BLE001
                return None
        else:
            # Read from local path
            try:
                content = Path(path_str).read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                return None

    # Generate outline from content
    return get_file_map_from_content(content, Path(path_str).name, max_tokens=max_tokens)


def get_file_map_from_content(content: str, filename: str, max_tokens: int = 2048) -> str | None:
    """Generate structure map from content without filesystem IO.

    Pure function for generating a file structure map when you already
    have the content in memory (e.g., from ACP embedded resources).

    Args:
        content: File content as string
        filename: Filename for language detection (e.g. "foo.py", "src/main.rs")
        max_tokens: Maximum tokens for output (approximate)

    Returns:
        Formatted structure map or None if language not supported
    """
    from grep_ast import TreeContext  # type: ignore[import-untyped]

    if not is_language_supported(filename):
        return None

    # Get definition tags
    tags = get_tags_from_content(content, filename)
    def_tags = [t for t in tags if t.kind == "def"]

    if not def_tags:
        return None

    # Build line ranges for rendering
    lois: list[int] = []
    line_ranges: dict[int, int] = {}

    for tag in def_tags:
        if tag.signature_end_line >= tag.line:
            lois.extend(range(tag.line, tag.signature_end_line + 1))
        else:
            lois.append(tag.line)
        if tag.end_line >= 0:
            line_ranges[tag.line] = tag.end_line

    # Render tree using TreeContext
    code = content if content.endswith("\n") else content + "\n"
    context = TreeContext(
        filename,
        code,
        child_context=False,
        last_line=False,
        margin=0,
        mark_lois=False,
        loi_pad=0,
        show_top_of_file_parent_scope=False,
    )
    context.add_lines_of_interest(lois)
    context.add_context()
    tree_output = _render_with_line_ranges(context, line_ranges)
    # Build final output with header
    lines = content.count("\n") + 1
    tokens_approx = len(tree_output) // 4
    header = (
        f"# File: {filename} ({lines} lines)\n"
        f"# Structure map (~{tokens_approx} tokens). Use read with line/limit for details.\n\n"
    )
    result = header + f"{filename}:\n" + tree_output
    # Truncate if needed
    max_chars = max_tokens * 4
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... [truncated]\n"
    return result


def _render_with_line_ranges(context: Any, line_ranges: dict[int, int]) -> str:
    """Render a TreeContext like ``format()``, marking definitions with their lines.

    Each shown definition line gets ``# [start-end]`` (1-based). Rendering here,
    instead of post-processing ``format()``, keeps the source line index, so
    identical signatures (e.g. several ``__init__``) get their own ranges.

    Args:
        context: grep-ast TreeContext after ``add_context()``
        line_ranges: Definition start line -> end line (0-based)
    """
    show_lines: set[int] = context.show_lines
    output_lines: dict[int, str] = context.output_lines
    out: list[str] = []
    dots = 0 not in show_lines
    for i, line in enumerate(context.lines):
        if i not in show_lines:
            if dots:
                out.append("⋮")
                dots = False
            continue
        rendered = f"│{output_lines.get(i, line)}"
        if (end := line_ranges.get(i)) is not None:
            span = f"{i + 1}-{end + 1}" if end > i else f"{i + 1}"
            rendered += f"  # [{span}]"
        out.append(rendered)
        dots = True
    return "".join(f"{line}\n" for line in out)
