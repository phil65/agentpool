"""Filesystem, VCS (git) and worktree routes for the request's location."""

import asyncio
import mimetypes
import os
from pathlib import Path
import re
from typing import Literal

import anyio
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from agentpool_server.opencode_v2_server.deps import DirectoryDep, V2Response, ok
from agentpool_server.opencode_v2_server.errors import (
    conflict,
    file_not_found,
    invalid_request,
    unavailable,
)
from agentpool_server.opencode_v2_server.location import normalize_directory, public_ref
from opencode_v2_sdk.models import (
    ExperimentalFsWriteResponse,
    FileDiffInfo,
    FileSystemEntry,
    FileSystemWrite,
    FsFindResponse,
    FsListResponse,
    VcsBase,
    VcsBaseResponse,
    VcsBranch,
    VcsBranchListResponse,
    VcsDiffResponse,
    VcsFileStatus,
    VcsGetResponse,
    VcsInfo,
    VcsStatusResponse,
    WorktreeCreateInput,
    WorktreeDirectory,
    WorktreeInfo,
    WorktreeList,
    WorktreeRefreshBody,
    WorktreeRemoveInput,
)


router = APIRouter(tags=["fs"], default_response_class=V2Response)

DEFAULT_FIND_LIMIT = 100
MAX_FIND_LIMIT = 1000
IGNORED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", ".mypy_cache"})


def resolve_path(directory: str, path: str) -> Path:
    """Resolve an absolute or location-relative path."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(directory) / candidate
    return Path(normalize_directory(str(candidate)))


# --- filesystem ----------------------------------------------------------------------------


@router.get("/api/fs/read/{path:path}")
async def fs_read(path: str, directory: DirectoryDep) -> Response:
    """Read a file (raw bytes)."""
    target = resolve_path(directory, "/" + path if not path.startswith("/") else path)
    if not target.is_file():
        target = resolve_path(directory, path)
    if not target.is_file():
        raise file_not_found(path)
    data = await anyio.Path(target).read_bytes()
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return Response(content=data, media_type=mime)


@router.get("/api/fs/list")
async def fs_list(directory: DirectoryDep, path: str | None = None) -> Response:
    """List a directory."""
    target = resolve_path(directory, path or ".")
    if not target.is_dir():
        raise file_not_found(path or directory)
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    data = [
        FileSystemEntry(path=str(entry), type="directory" if entry.is_dir() else "file")
        for entry in entries
    ]
    return ok(FsListResponse(location=public_ref(directory), data=data))


async def _git(cwd: str, *args: str) -> str | None:
    try:
        result = await anyio.run_process(["git", *args], cwd=cwd, check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode(errors="replace")


async def _candidate_files(directory: str) -> list[str]:
    listed = await _git(directory, "ls-files", "--cached", "--others", "--exclude-standard")
    if listed is not None:
        return [line for line in listed.splitlines() if line]
    return await asyncio.to_thread(_walk_files, directory)  # Off the loop: a large tree


def _walk_files(directory: str) -> list[str]:
    """Files below ``directory`` (relative, at most about 50k), ignored dirs skipped."""
    files: list[str] = []
    for root, dirs, names in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        rel_root = os.path.relpath(root, directory)
        files.extend(os.path.normpath(os.path.join(rel_root, name)) for name in names)  # noqa: PTH118
        if len(files) > 50_000:  # noqa: PLR2004
            break
    return files


def _fuzzy_score(query: str, candidate: str) -> int | None:
    """Simple subsequence score (lower is better), None if no match."""
    if not query:
        return len(candidate)
    lowered = candidate.lower()
    position = -1
    score = 0
    for char in query.lower():
        position = lowered.find(char, position + 1)
        if position < 0:
            return None
        score += position
    return score + len(candidate)


@router.get("/api/fs/find")
async def fs_find(
    directory: DirectoryDep,
    query: str = "",
    entry_type: Literal["file", "directory"] | None = Query(default=None, alias="type"),
    limit: str | None = None,
) -> Response:
    """Fuzzy-find files or directories below the location."""
    try:
        max_items = min(int(limit), MAX_FIND_LIMIT) if limit else DEFAULT_FIND_LIMIT
    except ValueError:
        raise invalid_request("limit must be an integer", field="limit") from None
    files = await _candidate_files(directory)
    # Scoring up to 50k paths is CPU work: off the loop (it runs per keystroke)
    ranked = await asyncio.to_thread(_rank, files, query, entry_type)
    data = [
        FileSystemEntry(path=str(Path(directory) / path), type=kind)
        for path, kind in ranked[:max_items]
    ]
    return ok(FsFindResponse(location=public_ref(directory), data=data))


def _rank(
    files: list[str], query: str, entry_type: Literal["file", "directory"] | None
) -> list[tuple[str, Literal["file", "directory"]]]:
    """The files (and their directories) matching ``query``, best first."""
    candidates: list[tuple[str, Literal["file", "directory"]]] = []
    if entry_type != "directory":
        candidates.extend((f, "file") for f in files)
    if entry_type != "file":
        dirs = {str(Path(f).parent) for f in files if str(Path(f).parent) != "."}
        candidates.extend((d, "directory") for d in sorted(dirs))
    scored = [
        (score, path, kind)
        for path, kind in candidates
        if (score := _fuzzy_score(query, path)) is not None
    ]
    scored.sort()
    return [(path, kind) for _, path, kind in scored]


@router.post("/api/experimental/fs/write")
async def fs_write(request: Request, directory: DirectoryDep, path: str) -> Response:
    """Write a file (raw request body), creating missing parent directories."""
    target = resolve_path(directory, path)
    data = await request.body()
    await anyio.Path(target.parent).mkdir(parents=True, exist_ok=True)
    await anyio.Path(target).write_bytes(data)
    return ok(
        ExperimentalFsWriteResponse(
            location=public_ref(directory), data=FileSystemWrite(path=str(target))
        )
    )


@router.post("/api/experimental/fs/watch", status_code=204)
async def fs_watch() -> Response:
    """File watching is not supported (clients fall back to polling)."""
    raise unavailable("fs.watch")


@router.delete("/api/experimental/fs/watch", status_code=204)
async def fs_unwatch() -> Response:
    """File watching is not supported."""
    raise unavailable("fs.watch")


# --- vcs -----------------------------------------------------------------------------------


async def _default_branch(directory: str) -> str | None:
    head = await _git(directory, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head:
        return head.strip().removeprefix("origin/")
    for name in ("main", "master"):
        if await _git(directory, "rev-parse", "--verify", "--quiet", name) is not None:
            return name
    return None


@router.get("/api/vcs")
async def vcs_get(directory: DirectoryDep) -> Response:
    """VCS provider and branches of the location."""
    if await _git(directory, "rev-parse", "--is-inside-work-tree") is None:
        info = VcsInfo(provider=None, branch=VcsBranch())
    else:
        current = await _git(directory, "branch", "--show-current")
        info = VcsInfo(
            provider="git",
            branch=VcsBranch(
                current=(current or "").strip() or None, default=await _default_branch(directory)
            ),
        )
    return ok(VcsGetResponse(location=public_ref(directory), data=info))


@router.post("/api/vcs/init", status_code=204)
async def vcs_init(directory: DirectoryDep, provider: str | None = None) -> Response:
    """Initialize a git repository at the location."""
    if provider not in {None, "git"}:
        raise invalid_request(f"Unsupported VCS provider: {provider}", field="provider")
    if await _git(directory, "init") is None:
        raise conflict("git init failed", resource=directory)
    return Response(status_code=204)


@router.get("/api/vcs/base")
async def vcs_base(directory: DirectoryDep) -> Response:
    """Review base: the default branch."""
    default = await _default_branch(directory)
    base = None
    if default and (ref := await _git(directory, "merge-base", "HEAD", default)):
        base = VcsBase(name=default, ref=ref.strip(), source="default")
    return ok(VcsBaseResponse(location=public_ref(directory), data=base))


@router.get("/api/vcs/branch")
async def vcs_branches(
    directory: DirectoryDep, search: str | None = None, limit: str | None = None
) -> Response:
    """Local branches."""
    output = await _git(directory, "branch", "--format=%(refname:short)", "--sort=-committerdate")
    branches = [b for b in (output or "").splitlines() if b]
    if search:
        branches = [b for b in branches if search.lower() in b.lower()]
    if limit and limit.isdigit():
        branches = branches[: int(limit)]
    return ok(VcsBranchListResponse(location=public_ref(directory), data=branches))


def _numstat(output: str) -> dict[str, tuple[int, int]]:
    stats: dict[str, tuple[int, int]] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:  # noqa: PLR2004
            added, deleted, name = parts
            stats[name] = (
                int(added) if added.isdigit() else 0,
                int(deleted) if deleted.isdigit() else 0,
            )
    return stats


def _status_kind(code: str) -> Literal["added", "deleted", "modified"]:
    if "D" in code:
        return "deleted"
    if "A" in code or "?" in code:
        return "added"
    return "modified"


@router.get("/api/vcs/status")
async def vcs_status(directory: DirectoryDep) -> Response:
    """Changed files of the working tree."""
    porcelain = await _git(directory, "status", "--porcelain=v1", "--untracked-files=all")
    stats = _numstat(await _git(directory, "diff", "HEAD", "--numstat") or "")
    data = []
    for line in (porcelain or "").splitlines():
        if len(line) < 4:  # noqa: PLR2004
            continue
        code, name = line[:2], line[3:].split(" -> ")[-1]
        added, deleted = stats.get(name, (0, 0))
        data.append(
            VcsFileStatus(file=name, additions=added, deletions=deleted, status=_status_kind(code))
        )
    return ok(VcsStatusResponse(location=public_ref(directory), data=data))


_DIFF_FILE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)


def parse_unified_diff(patch: str) -> list[FileDiffInfo]:
    """Split a multi-file unified diff into per-file diff infos."""
    starts = [m.start() for m in _DIFF_FILE.finditer(patch)]
    result: list[FileDiffInfo] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(patch)
        chunk = patch[start:end]
        match = _DIFF_FILE.match(chunk)
        if match is None:
            continue
        name = match.group(2)
        additions = sum(
            1 for line in chunk.splitlines() if line.startswith("+") and not line.startswith("+++")
        )
        deletions = sum(
            1 for line in chunk.splitlines() if line.startswith("-") and not line.startswith("---")
        )
        if "\nnew file mode" in chunk:
            status: Literal["added", "deleted", "modified"] = "added"
        elif "\ndeleted file mode" in chunk:
            status = "deleted"
        else:
            status = "modified"
        result.append(
            FileDiffInfo(
                file=name, patch=chunk, additions=additions, deletions=deletions, status=status
            )
        )
    return result


@router.get("/api/vcs/diff")
async def vcs_diff(
    directory: DirectoryDep,
    mode: Literal["working", "branch", "committed"] = "working",
    base: str | None = None,
    context: str | None = None,
    untracked: Literal["files", "folders"] | None = None,
) -> Response:
    """Diff of the working tree, the branch (vs. base) or committed changes."""
    unified = f"-U{context}" if context and context.isdigit() else "-U3"
    match mode:
        case "working":
            args = ["diff", unified, "HEAD"]
        case "branch":
            ref = base or await _default_branch(directory) or "HEAD"
            args = ["diff", unified, f"{ref}...HEAD"]
        case "committed":
            ref = base or await _default_branch(directory) or "HEAD"
            args = ["diff", unified, f"{ref}..HEAD"]
    patch = await _git(directory, *args) or ""
    data = parse_unified_diff(patch)
    if mode == "working" and untracked is not None:
        others = await _git(directory, "ls-files", "--others", "--exclude-standard") or ""
        for name in others.splitlines():
            if not name:
                continue
            file_path = Path(directory) / name
            try:
                lines = file_path.read_text(errors="replace").splitlines()
            except OSError:
                continue
            body = "\n".join(f"+{line}" for line in lines)
            header = (
                f"diff --git a/{name} b/{name}\nnew file mode 100644\n--- /dev/null\n+++ b/{name}\n"
            )
            hunk = f"@@ -0,0 +1,{len(lines)} @@\n" if lines else ""
            data.append(
                FileDiffInfo(
                    file=name,
                    patch=header + hunk + body,
                    additions=len(lines),
                    deletions=0,
                    status="added",
                )
            )
    return ok(VcsDiffResponse(location=public_ref(directory), data=data))


# --- worktrees -----------------------------------------------------------------------------


async def _worktrees(directory: str) -> list[WorktreeDirectory]:
    output = await _git(directory, "worktree", "list", "--porcelain") or ""
    return [
        WorktreeDirectory(directory=line.removeprefix("worktree ").strip(), strategy="git")
        for line in output.splitlines()
        if line.startswith("worktree ")
    ]


@router.get("/api/worktree")
async def worktree_list(directory: DirectoryDep, project_id: str | None = None) -> Response:
    """Git worktrees of the location's repository."""
    return ok(WorktreeList(await _worktrees(directory)))


@router.post("/api/worktree")
async def worktree_create(body: WorktreeCreateInput, directory: DirectoryDep) -> Response:
    """Create a git worktree (new branch from ``from`` or HEAD)."""
    toplevel = (await _git(directory, "rev-parse", "--show-toplevel") or "").strip()
    if not toplevel:
        raise conflict("Location is not a git repository", resource=directory)
    name = body.name or body.branch or f"worktree-{os.getpid()}"
    target = body.directory or str(Path(toplevel).parent / f"{Path(toplevel).name}-{name}")
    args = ["worktree", "add"]
    if body.branch:
        args += ["-b", body.branch]
    args.append(target)
    if body.from_:
        args.append(body.from_)
    if await _git(toplevel, *args) is None:
        raise conflict(f"Could not create worktree at {target}", resource=target)
    return ok(WorktreeInfo(directory=normalize_directory(target)))


@router.delete("/api/worktree", status_code=204)
async def worktree_remove(body: WorktreeRemoveInput, directory: DirectoryDep) -> Response:
    """Remove a git worktree."""
    args = ["worktree", "remove", *(["--force"] if body.force else []), body.directory]
    if await _git(directory, *args) is None:
        raise conflict(f"Could not remove worktree {body.directory}", resource=body.directory)
    return Response(status_code=204)


@router.post("/api/worktree/refresh", status_code=204)
async def worktree_refresh(
    directory: DirectoryDep, body: WorktreeRefreshBody | None = None
) -> Response:
    """Prune stale worktree metadata."""
    await _git(directory, "worktree", "prune")
    return Response(status_code=204)
