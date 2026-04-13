"""Helper functions for bash tool output formatting."""

from __future__ import annotations


def truncate_output(output: str, limit: int) -> tuple[str, bool]:
    """Truncate output to limit bytes, returning (output, was_truncated)."""
    if len(output.encode()) <= limit:
        return output, False
    truncated = output.encode()[-limit:].decode(errors="ignore")
    return f"...[truncated]\n{truncated}", True


def format_output(
    stdout: str,
    stderr: str,
    exit_code: int | None,
    truncated: bool,
    elapsed: float | None = None,
) -> str:
    """Format the final output string."""
    # Combine stdout and stderr
    output = stdout
    if stderr:
        output = f"{stdout}\n\nSTDERR:\n{stderr}" if stdout else f"STDERR:\n{stderr}"

    # Add metadata only when relevant
    suffix_parts = []
    if truncated:
        suffix_parts.append("[output truncated]")
    if exit_code and exit_code != 0:
        suffix_parts.append(f"Exit code: {exit_code}")
    if elapsed is not None:
        if elapsed < 1:
            suffix_parts.append(f"Execution time: {elapsed:.2f}s")
        else:
            suffix_parts.append(f"Execution time: {elapsed:.1f}s")

    if suffix_parts:
        return f"{output}\n\n{' | '.join(suffix_parts)}"
    return output
