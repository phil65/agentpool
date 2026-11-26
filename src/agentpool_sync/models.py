"""Core models for file sync metadata."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class SyncMetadata:
    """Metadata extracted from file header/frontmatter.

    Defines what a file depends on and which agent handles updates.
    """

    agent: str | None = None
    """Reference to agent name in manifest that handles reconciliation."""

    dependencies: list[str] = field(default_factory=list)
    """Glob patterns for files this depends on (e.g., 'src/models/*.py')."""

    urls: list[str] = field(default_factory=list)
    """Reference URLs the agent can consult."""

    context: dict[str, str] = field(default_factory=dict)
    """Additional context key-value pairs for the agent."""

    last_checked: str | None = None
    """Git commit hash when this file was last reconciled."""


@dataclass
class FileChange:
    """Represents a file that needs reconciliation."""

    path: str
    """Path to the file needing review."""

    metadata: SyncMetadata
    """Extracted sync metadata from the file."""

    changed_deps: list[str]
    """Paths of dependencies that changed since last check."""

    diff: str
    """Combined diff of all changed dependencies."""

    changed_urls: list[str] = field(default_factory=list)
    """URLs that changed since last check."""

    url_contents: dict[str, str] = field(default_factory=dict)
    """Mapping of changed URL -> fetched content."""


@dataclass
class UrlChange:
    """Represents a changed URL dependency."""

    url: str
    """The URL that changed."""

    old_hash: str | None
    """Previous content hash (None if new)."""

    new_hash: str
    """Current content hash."""

    content: str
    """Fetched content."""

    checked_at: datetime
    """When this was checked."""
