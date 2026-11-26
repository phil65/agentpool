"""Unified diff utilities for computing and analyzing text differences."""

import difflib


def compute_unified_diff(
    before: str,
    after: str,
    *,
    fromfile: str = "",
    tofile: str = "",
    ensure_trailing_newline: bool = False,
) -> str:
    """Compute a unified diff between two strings.

    Args:
        before: Original content
        after: Modified content
        fromfile: Name for the original file in diff header
        tofile: Name for the modified file in diff header
        ensure_trailing_newline: Ensure both inputs end with newline before diffing

    Returns:
        Unified diff as a string
    """
    if ensure_trailing_newline:
        if before and not before.endswith("\n"):
            before += "\n"
        if after and not after.endswith("\n"):
            after += "\n"

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(before_lines, after_lines, fromfile=fromfile, tofile=tofile)
    return "".join(diff_lines)


def get_changed_lines(
    before: str,
    after: str,
    *,
    path: str = "",
) -> list[str]:
    """Get only the added/removed lines from a unified diff.

    Returns lines starting with '+' or '-', including the diff header lines
    ('---' and '+++').

    Args:
        before: Original content
        after: Modified content
        path: File path for diff headers

    Returns:
        List of changed lines (additions and deletions)
    """
    diff = compute_unified_diff(before, after, fromfile=path, tofile=path)
    return [line for line in diff.splitlines() if line.startswith(("+", "-"))]


def count_changed_lines(diff_text: str) -> int:
    """Count the number of added/removed lines in a diff string.

    Args:
        diff_text: A unified diff string

    Returns:
        Number of lines starting with '+' or '-' (including headers)
    """
    return sum(1 for line in diff_text.splitlines() if line.startswith(("+", "-")))


def get_changed_line_numbers(original_content: str, new_content: str) -> list[int]:
    """Extract line numbers where changes occurred for UI highlighting.

    Uses SequenceMatcher to find changed blocks and returns line numbers
    in the new content where changes happened.

    Args:
        original_content: Original file content
        new_content: Modified file content

    Returns:
        List of line numbers (1-based) where changes occurred in new content
    """
    old_lines = original_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    changed_line_numbers: set[int] = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert", "delete"):
            if tag == "delete":
                line_num = min(j1 + 1, len(new_lines))
                if line_num > 0:
                    changed_line_numbers.add(line_num)
            else:
                for line_num in range(j1 + 1, j2 + 1):
                    changed_line_numbers.add(line_num)

    return sorted(changed_line_numbers)
