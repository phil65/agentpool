"""Main orchestrator for file sync operations."""

from dataclasses import replace
from datetime import datetime
from enum import Enum
import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentpool_sync.git import GitRepo
from agentpool_sync.models import FileChange
from agentpool_sync.packages import PackageRegistry
from agentpool_sync.parsers import BUILTIN_PARSERS
from agentpool_sync.resources import ResourceRegistry


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agentpool_sync.models import SyncMetadata
    from agentpool_sync.packages import PackageChange
    from agentpool_sync.parsers import MetadataParser

    ChangeHandler = Callable[[FileChange], Awaitable[str | None]]
    ProjectChangeHandler = Callable[[list[PackageChange]], Awaitable[None]]


class InitMode(Enum):
    """How to initialize files without a checkpoint."""

    FULL_HISTORY = "full_history"
    """Treat as needing review of all changes since first commit."""

    CURRENT = "current"
    """Mark as checked at current commit (opt-in without review)."""


class SyncManager:
    """Orchestrates file sync operations.

    Scans files for sync metadata, detects dependency changes via git,
    and triggers handlers for reconciliation. Checkpoints are stored
    in-file as part of the sync metadata.
    """

    def __init__(
        self,
        root: Path | str,
        registry_file: Path | str | None = None,
        packages_file: Path | str | None = None,
    ) -> None:
        """Initialize the sync manager.

        Args:
            root: Repository root path
            registry_file: Path to resource registry (default: .agentpool/resources.yml)
            packages_file: Path to packages registry (default: .agentpool/packages.yml)
        """
        self.root = Path(root).resolve()
        self.git = GitRepo(self.root)

        registry_path = (
            Path(registry_file) if registry_file else self.root / ".agentpool" / "resources.yml"
        )
        self.resources = ResourceRegistry(registry_file=registry_path)

        packages_path = (
            Path(packages_file) if packages_file else self.root / ".agentpool" / "packages.yml"
        )
        self.packages = PackageRegistry(registry_file=packages_path)

        self._parsers: dict[str, MetadataParser] = {}
        self._handlers: list[ChangeHandler] = []
        self._project_handlers: list[ProjectChangeHandler] = []

        for parser in BUILTIN_PARSERS:
            self.register_parser(parser)

    def register_parser(self, parser: MetadataParser) -> None:
        """Register a metadata parser for file extensions."""
        for ext in parser.extensions:
            self._parsers[ext] = parser

    def register_handler(self, handler: ChangeHandler) -> None:
        """Register a file change handler callback."""
        self._handlers.append(handler)

    def register_project_handler(self, handler: ProjectChangeHandler) -> None:
        """Register a project-level change handler (for package updates)."""
        self._project_handlers.append(handler)

    def _get_parser(self, path: Path) -> MetadataParser | None:
        """Get parser for a file based on extension."""
        return self._parsers.get(path.suffix)

    def _expand_globs(self, patterns: list[str]) -> set[Path]:
        """Expand glob patterns to actual file paths."""
        result: set[Path] = set()
        for pattern in patterns:
            matches = self.root.glob(pattern)
            result.update(matches)
        return result

    def _match_globs(self, path: str, patterns: list[str]) -> bool:
        """Check if a path matches any of the glob patterns."""
        return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)

    def scan_file(self, path: Path) -> tuple[Path, SyncMetadata] | None:
        """Extract sync metadata from a single file.

        Returns:
            Tuple of (path, metadata) or None if no metadata found
        """
        parser = self._get_parser(path)
        if not parser:
            return None

        try:
            content = path.read_text()
        except OSError, UnicodeDecodeError:
            return None

        metadata = parser.parse(content)
        if not metadata or not (metadata.dependencies or metadata.urls):
            return None

        return (path, metadata)

    def scan(self, paths: list[Path] | None = None) -> list[tuple[Path, SyncMetadata]]:
        """Scan files for sync metadata.

        Args:
            paths: Specific paths to scan (default: all supported files in repo)

        Returns:
            List of (path, metadata) tuples for files with sync metadata
        """
        if paths is None:
            paths = []
            for ext in self._parsers:
                paths.extend(self.root.rglob(f"*{ext}"))

        return [scanned for path in paths if (scanned := self.scan_file(path))]

    def _update_file_checkpoint(self, path: Path, commit: str) -> None:
        """Update the last_checked field in a file's sync metadata."""
        parser = self._get_parser(path)
        if not parser:
            return

        content = path.read_text()
        metadata = parser.parse(content)
        if not metadata:
            return

        updated_metadata = replace(metadata, last_checked=commit)
        new_content = parser.update(content, updated_metadata)
        path.write_text(new_content)

    def _get_commit_timestamp(self, commit: str) -> datetime:
        """Get timestamp for a commit."""
        output = self.git._run("show", "-s", "--format=%cI", commit)
        return datetime.fromisoformat(output.strip())

    def detect_changes(
        self,
        tracked_files: list[tuple[Path, SyncMetadata]] | None = None,
        init_mode: InitMode = InitMode.CURRENT,
    ) -> list[FileChange]:
        """Detect which tracked files have dependency changes.

        Args:
            tracked_files: Files to check (default: scan all)
            init_mode: How to handle files without a checkpoint

        Returns:
            List of FileChange objects for files needing reconciliation
        """
        if tracked_files is None:
            tracked_files = self.scan()

        current_commit = self.git.get_head_commit()
        changes: list[FileChange] = []

        for path, metadata in tracked_files:
            rel_path = str(path.relative_to(self.root))
            last_checked = metadata.last_checked

            # No checkpoint yet
            if not last_checked or not self.git.is_valid_commit(last_checked):
                if init_mode == InitMode.CURRENT:
                    # Skip - will be marked as current on next mark_checked
                    continue

                # FULL_HISTORY: needs review of everything
                changes.append(
                    FileChange(
                        path=rel_path,
                        metadata=metadata,
                        changed_deps=list(metadata.dependencies),
                        diff="(initial check - no previous checkpoint)",
                        changed_urls=list(metadata.urls),
                    )
                )
                continue

            # Check git dependencies
            changed_files = self.git.files_changed_between(last_checked, current_commit)
            matching_deps = [
                f for f in changed_files if self._match_globs(f, metadata.dependencies)
            ]

            diff = ""
            if matching_deps:
                diff = self.git.get_diff(matching_deps, last_checked, current_commit)

            # Check URL dependencies
            changed_urls: list[str] = []
            url_contents: dict[str, str] = {}
            if metadata.urls:
                commit_time = self._get_commit_timestamp(last_checked)
                for url in metadata.urls:
                    if self.resources.has_changed_since(url, commit_time):
                        changed_urls.append(url)
                        # Get cached content if available
                        if content := self.resources.get_content(url):
                            url_contents[url] = content

            # Only add if something changed
            if matching_deps or changed_urls:
                changes.append(
                    FileChange(
                        path=rel_path,
                        metadata=metadata,
                        changed_deps=matching_deps,
                        diff=diff,
                        changed_urls=changed_urls,
                        url_contents=url_contents,
                    )
                )

        return changes

    async def reconcile(
        self,
        changes: list[FileChange] | None = None,
        dry_run: bool = False,
        init_mode: InitMode = InitMode.CURRENT,
    ) -> list[tuple[FileChange, str | None]]:
        """Run handlers on files needing reconciliation.

        Args:
            changes: Changes to process (default: detect all)
            dry_run: If True, don't apply changes or update checkpoints
            init_mode: How to handle files without a checkpoint

        Returns:
            List of (change, result) tuples where result is new content or None
        """
        if changes is None:
            changes = self.detect_changes(init_mode=init_mode)

        results: list[tuple[FileChange, str | None]] = []
        current_commit = self.git.get_head_commit()

        for change in changes:
            result: str | None = None

            for handler in self._handlers:
                handler_result = await handler(change)
                if handler_result is not None:
                    result = handler_result
                    break

            results.append((change, result))

            if not dry_run:
                file_path = self.root / change.path
                if result:
                    file_path.write_text(result)
                # Update checkpoint in file
                self._update_file_checkpoint(file_path, current_commit)

        return results

    def mark_checked(
        self,
        paths: list[str] | None = None,
        commit: str | None = None,
    ) -> list[str]:
        """Update checkpoints for files (mark as reviewed without changes).

        Args:
            paths: Relative file paths to mark (default: all tracked files)
            commit: Commit hash to use (default: HEAD)

        Returns:
            List of paths that were updated
        """
        target_commit = commit or self.git.get_head_commit()
        updated: list[str] = []

        if paths is None:
            # Mark all tracked files
            tracked = self.scan()
            paths = [str(p.relative_to(self.root)) for p, _ in tracked]

        for rel_path in paths:
            file_path = self.root / rel_path
            if file_path.exists():
                self._update_file_checkpoint(file_path, target_commit)
                updated.append(rel_path)

        return updated

    def init(self, paths: list[str] | None = None) -> list[str]:
        """Initialize files at current commit (opt-in without full review).

        Convenience method equivalent to mark_checked with current HEAD.

        Args:
            paths: Relative file paths to init (default: all tracked files)

        Returns:
            List of paths that were initialized
        """
        return self.mark_checked(paths)

    async def refresh_urls(
        self,
        urls: list[str] | None = None,
    ) -> list[str]:
        """Refresh external URL resources and update registry.

        Args:
            urls: Specific URLs to refresh (default: all URLs from tracked files)

        Returns:
            List of URLs that changed
        """
        if urls is None:
            # Collect all URLs from tracked files
            tracked = self.scan()
            urls = []
            for _, metadata in tracked:
                urls.extend(metadata.urls)
            urls = list(set(urls))  # dedupe

        if not urls:
            return []

        changes = await self.resources.refresh(urls)
        return [c.url for c in changes]

    def get_url_content(self, url: str) -> str | None:
        """Get cached content for a URL (if available after refresh)."""
        return self.resources.get_content(url)

    # === Package tracking ===

    async def refresh_packages(
        self,
        packages: list[str] | None = None,
        fetch_notes: bool = True,
    ) -> list[PackageChange]:
        """Check for package version changes (minor/major only).

        Args:
            packages: Package names to check (default: all tracked)
            fetch_notes: Whether to fetch release notes

        Returns:
            List of PackageChange for packages with significant bumps
        """
        if packages is None:
            packages = self.packages.tracked_packages()

        if not packages:
            return []

        return await self.packages.refresh(packages, fetch_notes=fetch_notes)

    async def reconcile_packages(
        self,
        changes: list[PackageChange] | None = None,
        packages: list[str] | None = None,
    ) -> None:
        """Run project handlers for package changes.

        Args:
            changes: Changes to process (default: detect via refresh)
            packages: Packages to check if changes not provided
        """
        if changes is None:
            changes = await self.refresh_packages(packages)

        if not changes:
            return

        for handler in self._project_handlers:
            await handler(changes)

    def init_packages(self, packages: list[str]) -> list[str]:
        """Start tracking packages at current versions.

        Args:
            packages: Package names to track

        Returns:
            List of packages that were initialized
        """
        return self.packages.init(packages)

    def package_status(self) -> dict[str, dict[str, Any]]:
        """Get status of all tracked packages."""
        return self.packages.status()

    def status(self) -> dict[str, dict[str, Any]]:
        """Get sync status for all tracked files.

        Returns:
            Dict mapping file path to status info
        """
        current_commit = self.git.get_head_commit()
        tracked = self.scan()

        result: dict[str, dict[str, Any]] = {}
        for path, metadata in tracked:
            rel_path = str(path.relative_to(self.root))
            last_checked = metadata.last_checked

            status: dict[str, Any] = {
                "dependencies": metadata.dependencies,
                "urls": metadata.urls,
                "last_checked": last_checked,
                "current_commit": current_commit,
                "initialized": last_checked is not None,
            }

            if last_checked and self.git.is_valid_commit(last_checked):
                # Git changes
                changed_files = self.git.files_changed_between(last_checked, current_commit)
                matching = [f for f in changed_files if self._match_globs(f, metadata.dependencies)]

                # URL changes
                changed_urls: list[str] = []
                if metadata.urls:
                    commit_time = self._get_commit_timestamp(last_checked)
                    changed_urls = [
                        url
                        for url in metadata.urls
                        if self.resources.has_changed_since(url, commit_time)
                    ]

                status["needs_review"] = bool(matching) or bool(changed_urls)
                status["changed_deps"] = matching
                status["changed_urls"] = changed_urls
            else:
                status["needs_review"] = False  # Not initialized yet
                status["changed_deps"] = []
                status["changed_urls"] = []

            result[rel_path] = status

        return result
