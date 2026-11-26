"""Drop hidden and gitignored entries from a directory listing made for the agent.

Filesystem ``_find`` / ``_glob`` include hidden files (like fsspec), and only some
filesystems know about ``.gitignore``. Listings the agent sees (directory listings,
grep, diagnostics, skill files) leave out ``.git``, ``.venv`` and ignored build output,
so the filtering happens here, for any filesystem: hidden path segments below the
listed root are dropped everywhere; on a local filesystem, ignored paths too
(ripgrep's rules: ``.gitignore`` inside a git repository, ``.ignore``, ``.rgignore``).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Iterable

    from fsspec import AbstractFileSystem


def _local_path(fs: AbstractFileSystem, path: str) -> str | None:
    """The path on the local disk, if ``fs`` is a local filesystem (or a dir view of one)."""
    from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
    from fsspec.implementations.dirfs import DirFileSystem  # type: ignore[import-untyped]
    from fsspec.implementations.local import LocalFileSystem
    from upathtools.filesystems.base.wrapper import WrapperFileSystem

    current: Any = fs
    for _ in range(8):  # wrappers nest a few levels at most
        if isinstance(current, LocalFileSystem):
            return os.path.normpath(current._strip_protocol(path))
        if isinstance(current, AsyncFileSystemWrapper):
            current = current.sync_fs
        elif isinstance(current, WrapperFileSystem):  # same paths as the wrapped fs
            current = current.fs
        elif isinstance(current, DirFileSystem):
            path = current._join(path)
            current = current.fs
        else:
            return None
    return None


def _relative_parts(fs: AbstractFileSystem, root: str, path: str) -> tuple[str, ...] | None:
    root_s = fs._strip_protocol(root).rstrip("/")
    path_s = fs._strip_protocol(path).rstrip("/")
    if path_s == root_s:
        return ()
    prefix = f"{root_s}/" if root_s else ""
    if prefix and not path_s.startswith(prefix):
        return None
    return PurePosixPath(path_s[len(prefix) :]).parts


def _is_hidden(parts: tuple[str, ...]) -> bool:
    return any(part.startswith(".") and part not in {".", ".."} for part in parts)


async def filter_listing(
    fs: AbstractFileSystem,
    root: str,
    paths: Iterable[str],
) -> list[str]:
    """Return ``paths`` (in order) without hidden and, on local roots, ignored entries.

    Args:
        fs: The filesystem the paths come from.
        root: The listed directory; only segments below it count (listing a hidden
            directory itself works).
        paths: Paths returned by ``fs._find`` / ``fs._glob``.
    """
    candidates: list[tuple[str, tuple[str, ...]]] = []
    for path in paths:
        parts = _relative_parts(fs, root, path)
        if parts is not None and _is_hidden(parts):
            continue
        candidates.append((path, parts or ()))
    local_root = _local_path(fs, root)
    if local_root is None or not candidates or not Path(local_root).is_dir():
        return [path for path, _ in candidates]
    depth = max((len(parts) for _, parts in candidates), default=0)
    if depth == 0:
        return [path for path, _ in candidates]
    allowed = await asyncio.to_thread(_visible_local_paths, local_root, depth)
    kept: list[str] = []
    for path, parts in candidates:
        local = _local_path(fs, path)
        if not parts or local is None or local in allowed:
            kept.append(path)
    return kept


def _visible_local_paths(root: str, max_depth: int) -> set[str]:
    import ripgrep_rs

    found = ripgrep_rs.files(paths=[root], include_dirs=True, absolute=True, max_depth=max_depth)
    return {os.path.normpath(p) for p in found}


async def filter_listing_detail(
    fs: AbstractFileSystem,
    root: str,
    entries: dict[str, Any],
) -> dict[str, Any]:
    """Like `filter_listing`, for the ``detail=True`` form (path -> info)."""
    kept = await filter_listing(fs, root, entries)
    return {path: entries[path] for path in kept}
