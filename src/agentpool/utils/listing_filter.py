"""Drop hidden and gitignored entries from a directory listing made for the agent.

Filesystem ``_find`` / ``_glob`` include hidden files (like fsspec), and only some
filesystems know about ``.gitignore``. Listings the agent sees (directory listings,
grep, diagnostics, skill files) leave out ``.git``, ``.venv`` and ignored build output,
so the filtering happens here, for any filesystem: hidden path segments below the
listed root are dropped everywhere; on a local filesystem, ignored paths too
(ripgrep's rules: ``.gitignore`` inside a git repository, ``.ignore``, ``.rgignore``).
Paths through symlinks are kept when the symlink is (see ``_visible_local_paths``).
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
    locals_ = [(path, parts, _local_path(fs, path)) for path, parts in candidates]
    wanted = [local for _, parts, local in locals_ if parts and local is not None]
    allowed = await asyncio.to_thread(_visible_local_paths, local_root, depth, wanted)
    return [
        path for path, parts, local in locals_ if not parts or local is None or local in allowed
    ]


def _walk(root: str, max_depth: int) -> set[str]:
    """Paths below ``root`` ripgrep lists (as given: ``absolute`` would resolve symlinks)."""
    import ripgrep_rs

    found = ripgrep_rs.files(paths=[root], include_dirs=True, max_depth=max_depth)
    return {os.path.normpath(p) for p in found}


def _visible_local_paths(root: str, max_depth: int, wanted: Iterable[str]) -> set[str]:
    """The visible paths below ``root``, including the ``wanted`` ones behind symlinks.

    ripgrep doesn't follow symlinks: it lists a symlinked directory but nothing in it,
    and leaves out symlinked files. A wanted path whose parent is visible is decided
    here: below a symlinked directory by a walk from it (the target's own ignore files
    apply, the listed root's don't), a symlinked file is kept.
    """
    root = str(Path(root).absolute())  # Not resolved: the paths keep their symlinks
    allowed = _walk(root, max_depth)
    walked: set[str] = set()
    # Shallow ones first: a walk from a symlinked directory decides its descendants
    for path in sorted(wanted, key=lambda p: p.count(os.sep)):
        if path in allowed:
            continue
        parent = str(Path(path).parent)
        if parent != root and parent not in allowed:
            continue  # Below an ignored directory (or a hidden one)
        if parent != root and parent not in walked and Path(parent).is_symlink():
            walked.add(parent)
            depth_left = max_depth - len(Path(parent).relative_to(root).parts)
            allowed |= _walk(parent, max(depth_left, 1))
        elif Path(path).is_symlink() and not Path(path).is_dir():
            allowed.add(path)
    return allowed


async def filter_listing_detail(
    fs: AbstractFileSystem,
    root: str,
    entries: dict[str, Any],
) -> dict[str, Any]:
    """Like `filter_listing`, for the ``detail=True`` form (path -> info)."""
    kept = await filter_listing(fs, root, entries)
    return {path: entries[path] for path in kept}
