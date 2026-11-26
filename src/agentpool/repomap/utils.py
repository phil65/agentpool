"""Utility functions and constants for repomap."""

from pathlib import PurePosixPath
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem


# Important files that should be prioritized in repo map
ROOT_IMPORTANT_FILES: list[str] = [
    "requirements.txt",
    "setup.py",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "build.gradle",
    "pom.xml",
    "Makefile",
    "CMakeLists.txt",
    "Gemfile",
    "composer.json",
    ".env.example",
    "Dockerfile",
    "docker-compose.yml",
    "README.md",
    "README.rst",
    "README",
]

NORMALIZED_ROOT_IMPORTANT_FILES: set[str] = set(ROOT_IMPORTANT_FILES)

# Thresholds
MIN_TOKEN_SAMPLE_SIZE: int = 256
MIN_IDENT_LENGTH: int = 4
MAX_DEFINERS_THRESHOLD: int = 5


def is_important(fname: str) -> bool:
    """Check if a file is considered important (like config files)."""
    if fname in NORMALIZED_ROOT_IMPORTANT_FILES:
        return True
    basename = PurePosixPath(fname).name
    return basename in NORMALIZED_ROOT_IMPORTANT_FILES


def get_rel_path(path: str, root: str) -> str:
    """Get relative path from root.

    Args:
        path: Absolute path
        root: Root path

    Returns:
        Relative path from root (with forward slashes, also for Windows paths),
        or the original path if not under root
    """
    try:
        rel = PurePosixPath(path.replace("\\", "/")).relative_to(root.replace("\\", "/"))
        return str(rel)
    except ValueError:
        return path


def truncate_with_notice(
    path: str,
    content: str,
    head_lines: int = 100,
    tail_lines: int = 50,
) -> str:
    """Show head + tail for files where structure map isn't supported.

    Args:
        path: File path for display
        content: Full file content
        head_lines: Number of lines to show from start
        tail_lines: Number of lines to show from end

    Returns:
        Truncated content with notice about omitted lines
    """
    lines = content.splitlines()
    total = len(lines)

    if total <= head_lines + tail_lines:
        return content

    head = lines[:head_lines]
    tail = lines[-tail_lines:]
    omitted = total - head_lines - tail_lines

    return (
        f"# File: {path} ({total} lines)\n"
        f"# Showing first {head_lines} and last {tail_lines} lines "
        f"(language not supported for structure map)\n\n"
        + "\n".join(head)
        + f"\n\n... [{omitted} lines omitted] ...\n\n"
        + "\n".join(tail)
        + "\n\n# Use offset/limit params to read specific sections"
    )


async def find_src_files(fs: AsyncFileSystem, directory: str) -> list[str]:
    """Find all source files in a directory using async fsspec.

    Args:
        fs: Filesystem instance
        directory: Directory path to search

    Returns:
        List of file paths found
    """
    from upathtools import is_directory

    results: list[str] = []

    async def _recurse(path: str) -> None:
        try:
            entries = await fs._ls(path, detail=True)
        except OSError, FileNotFoundError:
            return

        for entry in entries:
            entry_path = entry.get("name", "")
            if await is_directory(fs, entry):
                await _recurse(entry_path)
            else:
                results.append(entry_path)

    await _recurse(directory)
    return results
