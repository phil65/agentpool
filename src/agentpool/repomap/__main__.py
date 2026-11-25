"""Command line interface: print a repository map.

Usage::

    python -m agentpool.repomap PATH [--tokens N] [--focus IDENT,...]
        [--focus-files GLOB,...] [--exclude GLOB,...] [--stats]

Only ``agentpool.repomap`` modules are imported (``agentpool`` itself imports lazily),
so startup stays light.
"""

from __future__ import annotations

import argparse
import asyncio
from fnmatch import fnmatch
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


SKIP_DIRS = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "site-packages",
})
"""Directories never searched (hidden directories are skipped as well)."""


def _split(values: Sequence[str] | None) -> list[str]:
    """Flatten repeated / comma-separated option values."""
    return [part.strip() for value in values or [] for part in value.split(",") if part.strip()]


def matches_any(rel_path: str, patterns: Iterable[str]) -> bool:
    """Whether a relative path matches one of the glob patterns.

    A pattern matches the whole relative path (``src/**/test_*.py``), a directory
    prefix (``docs`` matches ``docs/index.py``), or, if it has no slash, any single
    path component (``tests``, ``*_pb2.py``).
    """
    path = PurePosixPath(rel_path)
    for pattern in patterns:
        pattern = pattern.rstrip("/")  # noqa: PLW2901
        if path.full_match(pattern) or path.full_match(f"{pattern}/**"):
            return True
        if "/" not in pattern and any(fnmatch(part, pattern) for part in path.parts):
            return True
    return False


def _git_files(root: Path) -> list[str] | None:
    """Files under ``root`` that git does not ignore, or None outside a git repo."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [name for name in result.stdout.decode("utf-8", "replace").split("\0") if name]


def _walk_files(root: Path) -> list[str]:
    """All files under ``root``, skipping hidden and dependency / cache directories."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = Path(dirpath).relative_to(root)
        found.extend((rel_dir / name).as_posix() for name in filenames)
    return found


def find_source_files(root: Path, exclude: Sequence[str] = ()) -> list[str]:
    """Find source files with tree-sitter tag support below ``root``.

    Uses ``git ls-files`` (respects ``.gitignore``) inside a git work tree, else
    walks the directory. Dependency, cache and hidden directories are skipped.

    Args:
        root: Directory to search
        exclude: Glob patterns of paths to leave out (see ``matches_any``)

    Returns:
        Paths relative to ``root``, sorted
    """
    from agentpool.repomap.languages import is_language_supported

    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk_files(root)
    files = []
    for rel in candidates:
        parts = PurePosixPath(rel).parts
        if any(p in SKIP_DIRS or (p.startswith(".") and p not in {".", ".."}) for p in parts[:-1]):
            continue
        if exclude and matches_any(rel, exclude):
            continue
        if is_language_supported(parts[-1]) and (root / rel).is_file():
            files.append(rel)
    return sorted(files)


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m agentpool.repomap",
        description=(
            "Print a ranked map of a repository's code: the most referenced classes and "
            "functions (compact signatures, docstring summaries) within a token budget, "
            "plus a summary of the files left out."
        ),
    )
    parser.add_argument("path", type=Path, help="Directory to map")
    parser.add_argument(
        "-t", "--tokens", type=int, default=4096, help="Token budget (default: 4096)"
    )
    parser.add_argument(
        "-f",
        "--focus",
        action="append",
        metavar="IDENT,...",
        help="Identifiers to rank up (case-insensitive substrings of names and paths)",
    )
    parser.add_argument(
        "--focus-files",
        action="append",
        metavar="GLOB,...",
        help="Files to rank up (globs relative to PATH)",
    )
    parser.add_argument(
        "-x",
        "--exclude",
        action="append",
        metavar="GLOB,...",
        help="Paths to leave out entirely (e.g. tests, 'docs/**', '*_pb2.py')",
    )
    parser.add_argument("--stats", action="store_true", help="Print file / token counts to stderr")
    return parser


async def _generate(
    root: Path, files: list[str], tokens: int, focus: set[str], focus_files: set[str]
) -> tuple[str, int]:
    from upathtools.filesystems import AsyncLocalFileSystem

    from agentpool.repomap.core import RepoMap

    repo_map = RepoMap(AsyncLocalFileSystem(), root_path=root.as_posix(), max_tokens=tokens)
    abs_files = [(root / rel).as_posix() for rel in files]
    result = await repo_map.get_map_with_metadata(
        abs_files,
        boost_idents=focus,
        boost_files={(root / rel).as_posix() for rel in focus_files},
    )
    return result.content, result.included_files


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI."""
    args = build_parser().parse_args(argv)
    root: Path = args.path.resolve()
    if not root.is_dir():
        print(f"Not a directory: {args.path}", file=sys.stderr)
        return 2
    start = time.perf_counter()
    files = find_source_files(root, _split(args.exclude))
    if not files:
        print(f"No supported source files found in {args.path}", file=sys.stderr)
        return 1
    focus_globs = _split(args.focus_files)
    focus_files = {rel for rel in files if focus_globs and matches_any(rel, focus_globs)}
    content, shown = asyncio.run(
        _generate(root, files, args.tokens, set(_split(args.focus)), focus_files)
    )
    sys.stdout.write(content)
    if args.stats:
        print(
            f"{shown} of {len(files)} files shown, ~{len(content) / 4:.0f} tokens, "
            f"{time.perf_counter() - start:.2f} s",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
