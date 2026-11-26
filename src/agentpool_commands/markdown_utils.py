"""Markdown formatting utilities for command output."""

from typing import Any


def format_table(headers: list[str], rows: list[dict[str, Any]]) -> str:
    """Format data as a markdown table.

    Args:
        headers: Column headers
        rows: List of dicts with keys matching headers

    Returns:
        Markdown table string
    """
    if not rows:
        return ""

    # Create header row
    header_row = "| " + " | ".join(headers) + " |"
    separator_row = "|" + "|".join("---" for _ in headers) + "|"

    # Create data rows
    data_rows = []
    for row in rows:
        values = [str(row.get(header, "")) for header in headers]
        data_rows.append("| " + " | ".join(values) + " |")

    return "\n".join([header_row, separator_row, *data_rows])
