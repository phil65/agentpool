"""Type definitions for repomap module."""

from collections.abc import Callable
from dataclasses import dataclass


# Type aliases
type TokenCounter = Callable[[str], int]


@dataclass
class RepoMapResult:
    """Result of repository map generation with metadata."""

    content: str
    total_files_processed: int
    total_tags_found: int
    total_files_with_tags: int
    included_files: int
    included_tags: int
    truncated: bool
    coverage_ratio: float


@dataclass
class FileInfo:
    """Information about a file from fsspec."""

    path: str
    size: int
    mtime: float | None = None
    type: str = "file"
