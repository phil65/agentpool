"""FSSpec filesystem toolset helpers."""

from dataclasses import dataclass
import re
from typing import Any

from pydantic_ai import ModelRetry

from agentpool.log import get_logger


logger = get_logger(__name__)

# Default maximum size for file operations (64KB)
DEFAULT_MAX_SIZE = 64_000


@dataclass
class DiffHunk:
    """A single diff hunk representing one edit operation."""

    old_text: str
    """The text to find/replace (context + removed lines)."""

    new_text: str
    """The replacement text (context + added lines)."""

    raw: str
    """The raw diff text for this hunk."""


def apply_diff_hunks_streaming(
    original_content: str,
    diff_response: str,
    *,
    line_hint: int | None = None,
) -> str:
    """Apply locationless diff edits using streaming fuzzy matcher (Zed-style).

    Alternative to `apply_diff_hunks` that uses a dynamic programming based
    fuzzy matcher to locate where edits should be applied. This approach:
    - Uses line-by-line fuzzy matching with Levenshtein distance
    - Handles indentation differences gracefully
    - Can use line hints to disambiguate multiple matches
    - Matches Zed's edit resolution algorithm

    Args:
        original_content: The original file content
        diff_response: The agent's response containing diffs
        line_hint: Optional line number hint for disambiguation

    Returns:
        The modified content

    Raises:
        ModelRetry: If edits cannot be applied (for agent retry)
    """
    from sublime_search import StreamingFuzzyMatcher, parse_locationless_diff

    hunks = parse_locationless_diff(diff_response)
    if not hunks:
        logger.warning("No diff hunks found in response (streaming)")
        # Try falling back to structured edits format
        return apply_structured_edits(original_content, diff_response)

    content = original_content
    applied_edits = 0
    failed_hunks: list[str] = []
    ambiguous_hunks: list[str] = []
    for hunk in hunks:
        if not hunk.old_text.strip():
            # Pure insertion - skip for now (needs anchor point)
            logger.warning("Skipping pure insertion hunk (no context)")
            continue

        # Use streaming fuzzy matcher to find where old_text matches
        matcher = StreamingFuzzyMatcher(content)
        # Feed the old_text lines to the matcher
        # Simulate streaming by pushing line by line
        old_lines = hunk.old_text.split("\n")
        for i, line in enumerate(old_lines):
            # Add newline except for last line
            chunk = line + ("\n" if i < len(old_lines) - 1 else "")
            matcher.push(chunk, line_hint=line_hint)

        # Get final matches
        matches = matcher.finish()
        if not matches:
            logger.warning("No match found for hunk", hunk=hunk.old_text[:50])
            failed_hunks.append(hunk.old_text[:50])
            continue

        # Try to select best match
        best_match = matcher.select_best_match()
        if best_match is None and len(matches) > 1:
            # Multiple ambiguous matches
            logger.warning(
                "Ambiguous matches for hunk",
                hunk=hunk.old_text[:50],
                match_count=len(matches),
            )
            ambiguous_hunks.append(hunk.old_text[:50])
            continue

        # Use best match or first match
        match_range = best_match or matches[0]
        # Extract the matched text and replace with new_text
        matched_text = content[match_range.start : match_range.end]
        # Apply the replacement
        # We need to be careful about indentation - the matcher finds the range,
        # but we should preserve the original indentation structure
        old_indent = _get_leading_indent(matched_text)
        new_indent = _get_leading_indent(hunk.old_text)
        # Calculate indent delta
        indent_delta = len(old_indent) - len(new_indent)
        # Reindent new_text to match the file's indentation
        if indent_delta != 0:
            indent = old_indent[0] if old_indent else " "
            reindented_new = _reindent_text(hunk.new_text, indent_delta, indent)
        else:
            reindented_new = hunk.new_text

        # Apply the edit
        content = content[: match_range.start] + reindented_new + content[match_range.end :]
        applied_edits += 1

        logger.debug(
            "Applied streaming edit",
            start=match_range.start,
            end=match_range.end,
            old_len=len(matched_text),
            new_len=len(reindented_new),
        )

    if applied_edits == 0 and hunks:
        if ambiguous_hunks:
            matches_str = ", ".join(ambiguous_hunks[:3])
            msg = (
                f"Edit locations are ambiguous - multiple matches found for: {matches_str}... "
                "Please include more context lines in the diff to uniquely identify the location."
            )
        else:
            msg = (
                f"None of the {len(hunks)} diff hunks could be applied. "
                "The context lines don't match the current file content. "
                "Please read the file again and provide accurate diff context."
            )
        raise ModelRetry(msg)

    if failed_hunks or ambiguous_hunks:
        logger.warning(
            "Some hunks failed (streaming)",
            applied=applied_edits,
            failed=len(failed_hunks),
            ambiguous=len(ambiguous_hunks),
        )

    logger.info("Applied diff edits (streaming)", applied=applied_edits, total=len(hunks))
    return content


def _get_leading_indent(text: str) -> str:
    """Get the leading whitespace of the first non-empty line."""
    for line in text.split("\n"):
        if line.strip():
            return line[: len(line) - len(line.lstrip())]
    return ""


def _reindent_text(text: str, delta: int, indent_char: str = " ") -> str:
    """Reindent text by adding/removing leading whitespace.

    Args:
        text: Text to reindent
        delta: Number of characters to add (positive) or remove (negative)
        indent_char: Character to use for indentation (space or tab)

    Returns:
        Reindented text
    """
    if delta == 0:
        return text

    result_lines = []
    for line in text.split("\n"):
        if not line.strip():
            # Empty or whitespace-only line - keep as is
            result_lines.append(line)
            continue

        current_indent = len(line) - len(line.lstrip())
        if delta > 0:
            # Add indentation
            new_line = (indent_char * delta) + line
        else:
            # Remove indentation (but don't go negative)
            chars_to_remove = min(abs(delta), current_indent)
            new_line = line[chars_to_remove:]

        result_lines.append(new_line)

    return "\n".join(result_lines)


def apply_structured_edits(original_content: str, edits_response: str) -> str:
    """Apply structured edits from the agent response."""
    # Parse the edits from the response
    edits_match = re.search(r"<edits>(.*?)</edits>", edits_response, re.DOTALL)
    if not edits_match:
        logger.warning("No edits block found in response")
        return original_content

    edits_content = edits_match.group(1)
    # Find all old_text/new_text pairs
    old_texts = re.findall(r"<old_text[^>]*>(.*?)</old_text>", edits_content, re.DOTALL)
    new_texts = re.findall(r"<new_text>(.*?)</new_text>", edits_content, re.DOTALL)

    if len(old_texts) != len(new_texts):
        logger.warning("Mismatch between old_text and new_text blocks")
        return original_content

    # Apply edits sequentially
    content = original_content
    applied_edits = 0
    failed_matches = []
    multiple_matches = []
    for old_text, new_text in zip(old_texts, new_texts, strict=False):
        old_cleaned = old_text.strip()
        new_cleaned = new_text.strip()

        # Check for multiple matches (ambiguity)
        match_count = content.count(old_cleaned)
        if match_count > 1:
            multiple_matches.append(old_cleaned[:50])
        elif match_count == 1:
            content = content.replace(old_cleaned, new_cleaned, 1)
            applied_edits += 1
        else:
            failed_matches.append(old_cleaned[:50])

    # Raise ModelRetry for specific failure cases
    if applied_edits == 0 and len(old_cleaned) > 0:
        msg = (
            "Some edits were produced but none of them could be applied. "
            "Read the relevant sections of the file again so that "
            "I can perform the requested edits."
        )
        raise ModelRetry(msg)

    if multiple_matches:
        matches_str = ", ".join(multiple_matches)
        msg = (
            f"<old_text> matches multiple positions in the file: {matches_str}... "
            "Read the relevant sections of the file again and extend <old_text> "
            "to be more specific."
        )
        raise ModelRetry(msg)

    logger.info("Applied structured edits", num=applied_edits, total=len(old_texts))
    return content


def truncate_content(content: str, max_size: int = DEFAULT_MAX_SIZE) -> tuple[str, bool]:
    """Truncate text content to a maximum size in bytes.

    Args:
        content: Text content to truncate
        max_size: Maximum size in bytes (default: 64KB)

    Returns:
        Tuple of (truncated_content, was_truncated)
    """
    content_bytes = content.encode("utf-8")
    if len(content_bytes) <= max_size:
        return content, False

    # Truncate at byte boundary and decode safely
    truncated_bytes = content_bytes[:max_size]
    # Avoid breaking UTF-8 sequences by decoding with error handling
    truncated = truncated_bytes.decode("utf-8", errors="ignore")
    return truncated, True


def truncate_lines(
    lines: list[str], offset: int = 0, limit: int | None = None, max_bytes: int = DEFAULT_MAX_SIZE
) -> tuple[list[str], bool]:
    """Truncate lines with offset/limit and byte size constraints.

    Args:
        lines: List of text lines
        offset: Starting line index (0-based)
        limit: Maximum number of lines to include (None = no limit)
        max_bytes: Maximum total bytes (default: 64KB)

    Returns:
        Tuple of (truncated_lines, was_truncated)
    """
    # Apply offset (supports negative indexing like Python lists)
    start_idx = max(0, len(lines) + offset) if offset < 0 else min(offset, len(lines))
    if start_idx >= len(lines):
        return [], False
    # Apply line limit
    end_idx = min(len(lines), start_idx + limit) if limit is not None else len(lines)
    # Apply byte limit
    result_lines: list[str] = []
    total_bytes = 0
    for line in lines[start_idx:end_idx]:
        line_bytes = len(line.encode("utf-8"))
        if total_bytes + line_bytes > max_bytes:
            # Would exceed limit - this is actual truncation
            return result_lines, True

        result_lines.append(line)
        total_bytes += line_bytes

    # Successfully returned all requested content - not truncated
    # (byte truncation already handled above with early return)
    return result_lines, False


def format_size(size: int) -> str:
    """Format byte size as human-readable string."""
    if size < 1024:  # noqa: PLR2004
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def format_directory_listing(
    path: str,
    directories: list[dict[str, Any]],
    files: list[dict[str, Any]],
    pattern: str = "*",
) -> str:
    """Format directory listing as markdown table.

    Args:
        path: Base directory path
        directories: List of directory info dicts
        files: List of file info dicts
        pattern: Glob pattern used

    Returns:
        Formatted markdown string
    """
    lines = [f"## {path}"]
    if pattern != "*":
        lines.append(f"Pattern: `{pattern}`")
    lines.append("")

    if not directories and not files:
        lines.append("*Empty directory*")
        return "\n".join(lines)

    lines.append("| Name | Type | Size |")
    lines.append("|------|------|------|")

    # Directories first (sorted)
    for d in sorted(directories, key=lambda x: x["name"]):
        lines.append(f"| {d['name']}/ | dir | - |")  # noqa: PERF401

    # Then files (sorted)
    for f in sorted(files, key=lambda x: x["name"]):
        size_str = format_size(f.get("size", 0))
        lines.append(f"| {f['name']} | file | {size_str} |")

    lines.append("")
    lines.append(f"*{len(directories)} directories, {len(files)} files*")

    return "\n".join(lines)
