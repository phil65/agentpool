"""Registry for tracking external resource state (URLs, APIs, etc.)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import inspect
import shutil
from typing import TYPE_CHECKING, Any

import yaml


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@dataclass
class ResourceState:
    """State of an external resource."""

    content_hash: str
    """SHA256 hash of resource content."""

    last_checked: datetime
    """When this resource was last fetched/checked."""

    etag: str | None = None
    """HTTP ETag for conditional requests."""

    last_modified: str | None = None
    """HTTP Last-Modified header value."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for YAML storage."""
        last_checked = self.last_checked.isoformat()
        result: dict[str, Any] = {"content_hash": self.content_hash, "last_checked": last_checked}
        if self.etag:
            result["etag"] = self.etag
        if self.last_modified:
            result["last_modified"] = self.last_modified
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceState:
        """Deserialize from dict."""
        return cls(
            content_hash=data["content_hash"],
            last_checked=datetime.fromisoformat(data["last_checked"]),
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
        )


@dataclass
class ResourceChange:
    """Represents a changed external resource."""

    url: str
    """The resource URL."""

    old_state: ResourceState | None
    """Previous state (None if new resource)."""

    new_state: ResourceState
    """Current state after refresh."""

    content: str
    """The fetched content."""


@dataclass
class ResourceRegistry:
    """Central registry for external resource state.

    Tracks content hashes and metadata for URLs, enabling
    change detection across file sync boundaries. Caches
    fetched content on disk for later retrieval.
    """

    registry_file: Path
    """Path to the registry YAML file."""

    resources: dict[str, ResourceState] = field(default_factory=dict)
    """Mapping of URL -> state."""

    cache_dir: Path = field(init=False)
    """Directory for cached content."""

    def __post_init__(self) -> None:
        """Load existing state if file exists."""
        self.cache_dir = self.registry_file.parent / "resource_cache"
        if self.registry_file.exists():
            self._load()

    def _load(self) -> None:
        """Load registry from disk."""
        content = self.registry_file.read_text()
        data = yaml.safe_load(content) or {}
        resources = data.get("resources", {})
        self.resources = {url: ResourceState.from_dict(state) for url, state in resources.items()}

    def save(self) -> None:
        """Save registry to disk."""
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"resources": {url: state.to_dict() for url, state in self.resources.items()}}
        self.registry_file.write_text(yaml.dump(data, default_flow_style=False))

    def get(self, url: str) -> ResourceState | None:
        """Get state for a URL."""
        return self.resources.get(url)

    def has_changed_since(self, url: str, since: datetime) -> bool:
        """Check if a resource was updated after a given timestamp.

        Args:
            url: The resource URL
            since: Timestamp to compare against

        Returns:
            True if resource was checked after 'since' and hash changed,
            or if resource is not tracked.
        """
        state = self.resources.get(url)
        if not state:
            return True  # Unknown = assume changed
        return state.last_checked > since

    def _get_cache_path(self, url: str) -> Path:
        """Get cache file path for a URL."""
        return self.cache_dir / _url_to_cache_key(url)

    def _cache_content(self, url: str, content: str) -> None:
        """Cache content for a URL."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._get_cache_path(url).write_text(content)

    def get_content(self, url: str) -> str | None:
        """Get cached content for a URL.

        Args:
            url: The resource URL

        Returns:
            Cached content or None if not cached
        """
        cache_path = self._get_cache_path(url)
        if cache_path.exists():
            return cache_path.read_text()
        return None

    @staticmethod
    def hash_content(content: str | bytes) -> str:
        """Compute SHA256 hash of content."""
        if isinstance(content, str):
            content = content.encode()
        return f"sha256:{hashlib.sha256(content).hexdigest()}"

    async def refresh(
        self,
        urls: list[str],
        fetcher: Callable[[str], str] | None = None,
    ) -> list[ResourceChange]:
        """Refresh state for given URLs.

        Args:
            urls: URLs to refresh
            fetcher: Optional custom fetch function (default: httpx2)

        Returns:
            List of ResourceChange for URLs whose content changed
        """
        if fetcher is None:
            fetcher = _default_fetcher

        changes: list[ResourceChange] = []
        now = datetime.now(UTC)
        for url in urls:
            old_state = self.resources.get(url)

            try:
                content = await self._fetch(url, fetcher)
            except Exception:  # noqa: BLE001
                # Skip failed fetches, keep old state
                continue

            new_hash = self.hash_content(content)
            new_state = ResourceState(content_hash=new_hash, last_checked=now)
            self._cache_content(url, content)  # Always cache content
            # Check if changed
            if old_state is None or old_state.content_hash != new_hash:
                change = ResourceChange(
                    url=url,
                    old_state=old_state,
                    new_state=new_state,
                    content=content,
                )
                changes.append(change)

            self.resources[url] = new_state

        self.save()
        return changes

    async def _fetch(self, url: str, fetcher: Callable[[str], str]) -> str:
        """Fetch URL content using provided fetcher."""
        result = fetcher(url)
        if inspect.isawaitable(result):
            result = await result
        return str(result)

    def tracked_urls(self) -> list[str]:
        """Get all tracked URLs."""
        return list(self.resources.keys())

    def remove(self, url: str) -> bool:
        """Remove a URL from tracking (including cached content).

        Returns:
            True if URL was tracked and removed.
        """
        if url in self.resources:
            del self.resources[url]
            # Remove cached content
            cache_path = self._get_cache_path(url)
            if cache_path.exists():
                cache_path.unlink()
            self.save()
            return True
        return False

    def clear(self) -> None:
        """Remove all tracked resources and cached content."""
        self.resources.clear()
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
        self.save()


def _url_to_cache_key(url: str) -> str:
    """Convert URL to a safe filename for caching."""
    return hashlib.sha256(url.encode()).hexdigest()


def _default_fetcher(url: str) -> str:
    """Default sync fetcher using httpx2."""
    import httpx2

    response = httpx2.get(url, follow_redirects=True, timeout=30)
    response.raise_for_status()
    return response.text
