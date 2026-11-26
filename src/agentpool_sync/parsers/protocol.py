"""Parser protocol."""

from typing import TYPE_CHECKING, Protocol


if TYPE_CHECKING:
    from agentpool_sync.models import SyncMetadata


class MetadataParser(Protocol):
    """Protocol for file-type specific metadata extraction."""

    extensions: tuple[str, ...]
    """File extensions this parser handles."""

    def parse(self, content: str) -> SyncMetadata | None:
        """Extract sync metadata from file content."""
        ...

    def update(self, content: str, metadata: SyncMetadata) -> str:
        """Update/inject metadata back into file content."""
        ...
