"""File watcher utilities using watchfiles."""

import asyncio
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Self

import anyenv

from agentpool import log


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Set as AbstractSet

    from watchfiles import Change

    FileChangeCallback = Callable[[AbstractSet[tuple[Change, str]]], Awaitable[None]]


logger = log.get_logger(__name__)


@dataclass
class FileWatcher:
    """Async file watcher using watchfiles.

    Watches specified paths for changes and calls a callback when changes occur.

    Example:
        ```python
        async def on_change(changes):
            for change_type, path in changes:
                print(f"{change_type}: {path}")

        watcher = FileWatcher(
            paths=["/path/to/.git/HEAD"],
            callback=on_change,
        )

        async with watcher:
            # Watcher is running
            await asyncio.sleep(60)
        # Watcher stopped
        ```
    """

    paths: list[str | Path]
    """Paths to watch (files or directories)."""

    callback: FileChangeCallback
    """Async callback invoked when changes are detected."""

    debounce: int = 100
    """Debounce time in milliseconds."""

    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    """Background watch task."""

    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    """Event to signal stop."""

    async def start(self) -> None:
        """Start watching for file changes."""
        if self._task is not None:
            return  # Already running

        self._stop_event.clear()
        self._task = asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        """Stop watching for file changes."""
        if self._task is None:
            return

        self._stop_event.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _watch_loop(self) -> None:
        """Internal watch loop."""
        from watchfiles import awatch

        str_paths = [str(p) for p in self.paths]
        # Filter to only existing paths
        existing_paths = [p for p in str_paths if Path(p).exists()]
        if not existing_paths:
            logger.warning("FileWatcher: no existing paths", source=str_paths)
            return
        logger.info("FileWatcher: starting watch loop", source=existing_paths)
        try:
            async for changes in awatch(
                *existing_paths,
                debounce=self.debounce,
                stop_event=self._stop_event,
            ):
                logger.info("FileWatcher detected changes", changes=changes)
                # Don't let callback errors kill the watcher
                try:
                    await self.callback(changes)
                except Exception:
                    logger.exception("Error in file watcher callback")
        except Exception:
            logger.exception("FileWatcher watch loop failed")

    async def __aenter__(self) -> Self:
        """Start watcher on context enter."""
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Stop watcher on context exit."""
        await self.stop()


async def get_git_branch(repo_path: str | Path) -> str | None:
    """Get the current git branch name.

    Args:
        repo_path: Path to the git repository

    Returns:
        Branch name or None if not a git repo or on detached HEAD

    TODO: For remote/ACP support, this should accept an optional ExecutionEnvironment
    and use env.execute_command() instead of subprocess. This would allow git commands
    to run on the client side where the repository lives.
    """
    try:
        cmd = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
        proc = await anyenv.create_process(*cmd, cwd=str(repo_path), stdout="pipe", stderr="pipe")
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
    except OSError:
        return None
    else:
        branch = stdout.decode().strip()
        return branch if branch != "HEAD" else None


@dataclass
class GitBranchWatcher:
    """Watches for git branch changes using polling.

    Polls the current git branch periodically and calls a callback when it changes.
    Uses polling instead of file watching because git uses atomic renames which
    are not reliably detected by inotify/watchfiles.

    Example:
        ```python
        async def on_branch_change(branch: str | None):
            print(f"Branch changed to: {branch}")

        watcher = GitBranchWatcher(
            repo_path="/path/to/repo",
            callback=on_branch_change,
        )

        async with watcher:
            await asyncio.sleep(60)
        ```

    TODO: For remote/ACP support, this should accept an ExecutionEnvironment
    and run git commands through env.execute_command(). The polling would still
    happen server-side, but the git commands would execute on the client.
    """

    repo_path: str | Path
    """Path to the git repository."""

    callback: Callable[[str | None], Awaitable[None]]
    """Async callback invoked with new branch name when branch changes."""

    poll_interval: float = 1.0
    """Polling interval in seconds."""

    _current_branch: str | None = field(default=None, repr=False)
    """Cached current branch."""

    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    """Background polling task."""

    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    """Event to signal stop."""

    async def start(self) -> None:
        """Start watching for branch changes."""
        if self._task is not None:
            return  # Already running

        repo = Path(self.repo_path)
        git_dir = repo / ".git"
        # Handle git worktrees - .git might be a file pointing to the real git dir
        if git_dir.is_file():
            content = git_dir.read_text().strip()
            if content.startswith("gitdir:"):
                git_dir = Path(content[7:].strip())

        if not git_dir.exists():
            logger.warning("Git directory not found", git_dir=git_dir)
            return

        # Get initial branch
        self._current_branch = await get_git_branch(self.repo_path)
        msg = "GitBranchWatcher started (polling)"
        logger.info(msg, repo=self.repo_path, initial_branch=self._current_branch)
        self._stop_event.clear()
        self._task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        """Internal polling loop."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
                break  # Stop event was set
            except TimeoutError:
                # Poll interval elapsed, check for changes
                pass

            try:
                new_branch = await get_git_branch(self.repo_path)
                if new_branch != self._current_branch:
                    logger.info("Branch changed", before=self._current_branch, after=new_branch)
                    self._current_branch = new_branch
                    await self.callback(new_branch)
            except Exception:
                logger.exception("Error polling git branch")

    async def stop(self) -> None:
        """Stop watching for branch changes."""
        if self._task is None:
            return

        self._stop_event.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    @property
    def current_branch(self) -> str | None:
        """Get the current cached branch name."""
        return self._current_branch

    async def __aenter__(self) -> Self:
        """Start watcher on context enter."""
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Stop watcher on context exit."""
        await self.stop()
