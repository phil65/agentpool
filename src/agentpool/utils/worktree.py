"""Git worktree management for sandboxed agent work."""

import asyncio
from pathlib import Path
import random
import shutil

from agentpool.log import get_logger


logger = get_logger(__name__)

_ADJECTIVES = [
    "brave",
    "calm",
    "clever",
    "cosmic",
    "crisp",
    "curious",
    "eager",
    "gentle",
    "glowing",
    "happy",
    "hidden",
    "jolly",
    "kind",
    "lucky",
    "mighty",
    "misty",
    "nimble",
    "playful",
    "proud",
    "quick",
    "quiet",
    "shiny",
    "silent",
    "stellar",
    "sunny",
    "swift",
    "tidy",
    "witty",
]

_NOUNS = [
    "cabin",
    "cactus",
    "canyon",
    "circuit",
    "comet",
    "eagle",
    "engine",
    "falcon",
    "forest",
    "garden",
    "harbor",
    "island",
    "knight",
    "lagoon",
    "meadow",
    "moon",
    "mountain",
    "nebula",
    "orchid",
    "otter",
    "panda",
    "pixel",
    "planet",
    "river",
    "rocket",
    "sailor",
    "squid",
    "star",
    "tiger",
    "wizard",
    "wolf",
]


def _random_name() -> str:
    return f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}"


async def _run_git(
    *args: str,
    cwd: str,
) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    assert proc.returncode is not None
    return proc.returncode, stdout_bytes.decode().strip(), stderr_bytes.decode().strip()


async def create_worktree(
    repo_dir: str,
    name: str | None = None,
) -> tuple[str, str, str]:
    """Create a new git worktree.

    Args:
        repo_dir: Path to the main repository.
        name: Optional worktree name. Auto-generated if not provided.

    Returns:
        Tuple of (name, branch, directory).

    Raises:
        RuntimeError: If worktree creation fails.
    """
    worktree_root = Path(repo_dir) / ".worktrees"
    worktree_root.mkdir(parents=True, exist_ok=True)

    # Find a unique name
    for _ in range(25):
        candidate = name or _random_name()
        branch = f"opencode/{candidate}"
        directory = worktree_root / candidate

        if directory.exists():
            if name:
                msg = f"Worktree directory already exists: {directory}"
                raise RuntimeError(msg)
            continue

        # Check if branch already exists
        rc, _, _ = await _run_git(
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            cwd=repo_dir,
        )
        if rc == 0:
            if name:
                msg = f"Branch already exists: {branch}"
                raise RuntimeError(msg)
            continue

        # Create the worktree
        rc, stdout, stderr = await _run_git(
            "worktree",
            "add",
            "--no-checkout",
            "-b",
            branch,
            str(directory),
            cwd=repo_dir,
        )
        if rc != 0:
            msg = f"Failed to create worktree: {stderr or stdout}"
            raise RuntimeError(msg)

        # Populate it
        rc, stdout, stderr = await _run_git("reset", "--hard", cwd=str(directory))
        if rc != 0:
            msg = f"Failed to populate worktree: {stderr or stdout}"
            raise RuntimeError(msg)

        logger.info("Created worktree", name=candidate, branch=branch, directory=str(directory))
        return candidate, branch, str(directory)

    msg = "Failed to generate a unique worktree name after 25 attempts"
    raise RuntimeError(msg)


async def list_worktrees(repo_dir: str) -> list[str]:
    """List worktree directories.

    Args:
        repo_dir: Path to the main repository.

    Returns:
        List of worktree directory paths (excluding the main worktree).
    """
    rc, stdout, stderr = await _run_git("worktree", "list", "--porcelain", cwd=repo_dir)
    if rc != 0:
        logger.warning("Failed to list worktrees", error=stderr)
        return []

    directories: list[str] = []
    main_resolved = str(Path(repo_dir).resolve())
    for line in stdout.splitlines():
        if line.startswith("worktree "):
            path = line.removeprefix("worktree ").strip()
            # Skip the main worktree
            if str(Path(path).resolve()) != main_resolved:
                directories.append(path)
    return directories


async def remove_worktree(repo_dir: str, directory: str) -> None:
    """Remove a git worktree and its branch.

    Args:
        repo_dir: Path to the main repository.
        directory: Worktree directory to remove.

    Raises:
        RuntimeError: If removal fails.
    """
    # Find the branch name before removing
    rc, stdout, _ = await _run_git("worktree", "list", "--porcelain", cwd=repo_dir)
    branch: str | None = None
    resolved = str(Path(directory).resolve())
    if rc == 0:
        current_path: str | None = None
        for line in stdout.splitlines():
            if line.startswith("worktree "):
                current_path = str(Path(line.removeprefix("worktree ").strip()).resolve())
            elif line.startswith("branch ") and current_path == resolved:
                branch = line.removeprefix("branch ").strip().removeprefix("refs/heads/")

    # Remove the worktree
    rc, stdout, stderr = await _run_git("worktree", "remove", "--force", directory, cwd=repo_dir)
    if rc != 0:
        # If git worktree remove fails, try manual cleanup
        path = Path(directory)
        if path.exists():
            shutil.rmtree(path)
        else:
            msg = f"Failed to remove worktree: {stderr or stdout}"
            raise RuntimeError(msg)

    # Clean up the branch
    if branch:
        rc, stdout, stderr = await _run_git("branch", "-D", branch, cwd=repo_dir)
        if rc != 0:
            logger.warning("Failed to delete branch", branch=branch, error=stderr or stdout)

    logger.info("Removed worktree", directory=directory, branch=branch)


async def reset_worktree(repo_dir: str, directory: str) -> None:
    """Reset a worktree to the default branch.

    Args:
        repo_dir: Path to the main repository.
        directory: Worktree directory to reset.

    Raises:
        RuntimeError: If reset fails.
    """
    resolved = str(Path(directory).resolve())
    main_resolved = str(Path(repo_dir).resolve())
    if resolved == main_resolved:
        msg = "Cannot reset the primary workspace"
        raise RuntimeError(msg)

    # Determine reset target: try remote default, fall back to local main/master
    target: str | None = None

    # Try remote HEAD
    rc, stdout, _ = await _run_git("symbolic-ref", "refs/remotes/origin/HEAD", cwd=repo_dir)
    if rc == 0:
        remote_ref = stdout.removeprefix("refs/remotes/")
        target = remote_ref

    if not target:
        # Fall back to local main or master
        for branch in ("main", "master"):
            rc, _, _ = await _run_git(
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
                cwd=repo_dir,
            )
            if rc == 0:
                target = branch
                break

    if not target:
        msg = "Default branch not found"
        raise RuntimeError(msg)

    # Fetch if using remote target
    if "/" in target:
        remote = target.split("/", 1)[0]
        remote_branch = target.split("/", 1)[1]
        rc, _, stderr = await _run_git("fetch", remote, remote_branch, cwd=repo_dir)
        if rc != 0:
            msg = f"Failed to fetch {target}: {stderr}"
            raise RuntimeError(msg)

    # Reset
    rc, _, stderr = await _run_git("reset", "--hard", target, cwd=directory)
    if rc != 0:
        msg = f"Failed to reset worktree: {stderr}"
        raise RuntimeError(msg)

    # Clean
    rc, _, stderr = await _run_git("clean", "-ffdx", cwd=directory)
    if rc != 0:
        msg = f"Failed to clean worktree: {stderr}"
        raise RuntimeError(msg)

    logger.info("Reset worktree", directory=directory, target=target)
