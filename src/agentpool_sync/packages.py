"""Registry for tracking Python package versions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version as get_version
from typing import TYPE_CHECKING, Any

import anyenv
import yaml


if TYPE_CHECKING:
    from pathlib import Path


def is_significant_bump(old: str, new: str) -> bool:
    """Check if minor or major version changed (not just patch).

    Args:
        old: Previous version string
        new: New version string

    Returns:
        True if major or minor version increased
    """
    from packaging.version import Version

    try:
        o, n = Version(old), Version(new)
    except Exception:  # noqa: BLE001
        # If version parsing fails, assume significant
        return old != new
    else:
        return (n.major, n.minor) > (o.major, o.minor)


@dataclass
class PackageState:
    """State of a tracked package."""

    version: str
    """Currently tracked version."""

    last_checked: datetime
    """When this package was last checked."""

    changelog_url: str | None = None
    """URL to changelog/release notes."""

    release_notes: str | None = None
    """Cached release notes content."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for YAML storage."""
        result: dict[str, Any] = {
            "version": self.version,
            "last_checked": self.last_checked.isoformat(),
        }
        if self.changelog_url:
            result["changelog_url"] = self.changelog_url
        if self.release_notes:
            result["release_notes"] = self.release_notes
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageState:
        """Deserialize from dict."""
        return cls(
            version=data["version"],
            last_checked=datetime.fromisoformat(data["last_checked"]),
            changelog_url=data.get("changelog_url"),
            release_notes=data.get("release_notes"),
        )


@dataclass
class PackageChange:
    """Represents a package version change."""

    package: str
    """Package name."""

    old_version: str | None
    """Previous version (None if newly tracked)."""

    new_version: str
    """Current installed version."""

    release_notes: str | None = None
    """Fetched release notes if available."""

    changelog_url: str | None = None
    """URL to full changelog."""


@dataclass
class PackageRegistry:
    """Central registry for tracking Python package versions.

    Detects version changes and fetches release notes for context.
    Only triggers on minor/major version bumps, not patches.
    """

    registry_file: Path
    """Path to the registry YAML file."""

    packages: dict[str, PackageState] = field(default_factory=dict)
    """Mapping of package name -> state."""

    def __post_init__(self) -> None:
        """Load existing state if file exists."""
        if self.registry_file.exists():
            self._load()

    def _load(self) -> None:
        """Load registry from disk."""
        content = self.registry_file.read_text()
        data = yaml.safe_load(content) or {}
        packages = data.get("packages", {})
        self.packages = {name: PackageState.from_dict(state) for name, state in packages.items()}

    def save(self) -> None:
        """Save registry to disk."""
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"packages": {name: state.to_dict() for name, state in self.packages.items()}}
        self.registry_file.write_text(yaml.dump(data, default_flow_style=False))

    def get(self, package: str) -> PackageState | None:
        """Get state for a package."""
        return self.packages.get(package)

    @staticmethod
    def get_installed_version(package: str) -> str | None:
        """Get currently installed version of a package.

        Args:
            package: Package name

        Returns:
            Version string or None if not installed
        """
        try:
            return get_version(package)
        except PackageNotFoundError:
            return None

    async def refresh(
        self,
        packages: list[str],
        fetch_notes: bool = True,
    ) -> list[PackageChange]:
        """Check for package version changes.

        Args:
            packages: Package names to check
            fetch_notes: Whether to fetch release notes for changes

        Returns:
            List of PackageChange for packages with significant version bumps
        """
        changes: list[PackageChange] = []
        now = datetime.now(UTC)

        for package in packages:
            installed = self.get_installed_version(package)
            if not installed:
                continue

            old_state = self.packages.get(package)
            old_version = old_state.version if old_state else None
            # Check if significant change
            if old_version and not is_significant_bump(old_version, installed):
                # Update last_checked but don't report as change
                url = old_state.changelog_url if old_state else None
                state = PackageState(version=installed, last_checked=now, changelog_url=url)
                self.packages[package] = state
                continue
            # Significant change (or new package)
            release_notes: str | None = None
            changelog_url: str | None = None

            if fetch_notes:
                release_notes, changelog_url = await _fetch_release_notes(package, installed)

            change = PackageChange(
                package=package,
                old_version=old_version,
                new_version=installed,
                release_notes=release_notes,
                changelog_url=changelog_url,
            )
            changes.append(change)

            self.packages[package] = PackageState(
                version=installed,
                last_checked=now,
                changelog_url=changelog_url,
                release_notes=release_notes,
            )

        self.save()
        return changes

    def init(
        self,
        packages: list[str],
    ) -> list[str]:
        """Initialize tracking for packages at current versions.

        Args:
            packages: Package names to start tracking

        Returns:
            List of packages that were initialized
        """
        initialized: list[str] = []
        now = datetime.now(UTC)

        for package in packages:
            installed = self.get_installed_version(package)
            if not installed:
                continue

            self.packages[package] = PackageState(
                version=installed,
                last_checked=now,
            )
            initialized.append(package)

        self.save()
        return initialized

    def tracked_packages(self) -> list[str]:
        """Get all tracked package names."""
        return list(self.packages.keys())

    def remove(self, package: str) -> bool:
        """Remove a package from tracking.

        Returns:
            True if package was tracked and removed.
        """
        if package in self.packages:
            del self.packages[package]
            self.save()
            return True
        return False

    def clear(self) -> None:
        """Remove all tracked packages."""
        self.packages.clear()
        self.save()

    def status(self) -> dict[str, dict[str, Any]]:
        """Get status of all tracked packages.

        Returns:
            Dict mapping package name to status info
        """
        result: dict[str, dict[str, Any]] = {}
        for package, state in self.packages.items():
            installed = self.get_installed_version(package)

            status: dict[str, Any] = {
                "tracked_version": state.version,
                "installed_version": installed,
                "last_checked": state.last_checked.isoformat(),
                "changelog_url": state.changelog_url,
            }

            if installed and installed != state.version:
                status["needs_review"] = is_significant_bump(state.version, installed)
                status["is_patch_only"] = not status["needs_review"]
            else:
                status["needs_review"] = False
                status["is_patch_only"] = False

            result[package] = status

        return result


async def _fetch_pypi_info(package: str) -> dict[str, Any] | None:
    """Fetch package info from PyPI."""
    try:
        url = f"https://pypi.org/pypi/{package}/json"
        return await anyenv.get_json(url, timeout=30, return_type=dict)
    except Exception:  # noqa: BLE001
        return None


async def _fetch_release_notes(package: str, version: str) -> tuple[str | None, str | None]:
    """Try to fetch release notes for a package version.

    Args:
        package: Package name
        version: Version to get notes for

    Returns:
        Tuple of (release_notes, changelog_url)
    """
    pypi_info = await _fetch_pypi_info(package)
    if not pypi_info:
        return None, None

    info = pypi_info.get("info", {})
    project_urls = info.get("project_urls", {}) or {}

    changelog_url: str | None = None  # Try to find changelog URL
    for key in ("Changelog", "Changes", "Release Notes", "History"):
        if key in project_urls:
            changelog_url = project_urls[key]
            break

    # Try GitHub releases if we have a repo URL
    release_notes: str | None = None
    for key in ("Repository", "Source", "Homepage"):
        url = project_urls.get(key, "")
        if "github.com" in url:
            release_notes = await _fetch_github_release(url, version)
            if release_notes:
                break

    return release_notes, changelog_url


async def _fetch_github_release(repo_url: str, version: str) -> str | None:
    """Fetch release notes from GitHub.

    Args:
        repo_url: GitHub repository URL
        version: Version tag to look for

    Returns:
        Release notes or None
    """
    # Extract owner/repo from URL
    # https://github.com/owner/repo or https://github.com/owner/repo.git
    expected_parts = 2
    parts = repo_url.rstrip("/").removesuffix(".git").split("github.com/")
    if len(parts) != expected_parts:
        return None

    repo_path = parts[1]
    # Try common tag formats
    tag_formats = [f"v{version}", version, f"V{version}"]
    for tag in tag_formats:
        try:
            url = f"https://api.github.com/repos/{repo_path}/releases/tags/{tag}"
            headers = {"Accept": "application/vnd.github.v3+json"}
            data = await anyenv.get_json(url, timeout=30, headers=headers, return_type=dict)
            return data.get("body")
        except Exception:  # noqa: BLE001
            continue

    return None
