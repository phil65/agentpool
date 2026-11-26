"""Git integration for detecting dependency changes."""

from pathlib import Path
import subprocess


class GitError(Exception):
    """Git operation failed."""


class GitRepo:
    """Git repository wrapper for change detection."""

    def __init__(self, root: Path | str) -> None:
        """Initialize with repository root path.

        Args:
            root: Path to git repository root
        """
        self.root = Path(root).resolve()

    def _run(self, *args: str) -> str:
        """Run a git command and return stdout."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise GitError(f"Git command failed: {e.stderr}") from e
        except FileNotFoundError as e:
            raise GitError("Git executable not found") from e
        return result.stdout.strip()

    def get_head_commit(self) -> str:
        """Get current HEAD commit hash."""
        return self._run("rev-parse", "HEAD")

    def get_short_commit(self, commit: str | None = None) -> str:
        """Get short commit hash."""
        target = commit or "HEAD"
        return self._run("rev-parse", "--short", target)

    def is_valid_commit(self, commit: str) -> bool:
        """Check if a commit hash is valid."""
        try:
            self._run("rev-parse", "--verify", commit)
        except GitError:
            return False
        return True

    def files_changed_between(self, old_commit: str, new_commit: str | None = None) -> set[str]:
        """Get set of files that changed between two commits.

        Args:
            old_commit: Base commit hash
            new_commit: Target commit hash (defaults to HEAD)

        Returns:
            Set of relative file paths that changed
        """
        target = new_commit or "HEAD"
        output = self._run("diff", "--name-only", old_commit, target)
        if not output:
            return set()
        return {line.strip() for line in output.splitlines() if line.strip()}

    def get_diff(
        self,
        paths: list[str],
        since_commit: str,
        until_commit: str | None = None,
    ) -> str:
        """Get unified diff for specified paths between commits.

        Args:
            paths: File paths to get diff for
            since_commit: Base commit
            until_commit: Target commit (defaults to HEAD)

        Returns:
            Unified diff output
        """
        target = until_commit or "HEAD"
        if not paths:
            return ""
        return self._run("diff", since_commit, target, "--", *paths)

    def get_file_at_commit(self, path: str, commit: str) -> str | None:
        """Get file content at a specific commit.

        Args:
            path: Relative file path
            commit: Commit hash

        Returns:
            File content or None if file didn't exist
        """
        try:
            return self._run("show", f"{commit}:{path}")
        except GitError:
            return None

    def is_dirty(self, path: str | None = None) -> bool:
        """Check if working tree (or specific file) has uncommitted changes."""
        args = ["status", "--porcelain"]
        if path:
            args.extend(["--", path])
        output = self._run(*args)
        return bool(output)

    def get_root(self) -> Path:
        """Get the repository root directory."""
        output = self._run("rev-parse", "--show-toplevel")
        return Path(output)
