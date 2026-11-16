"""Resource information model with read capability."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self


if TYPE_CHECKING:
    from mcp.types import Resource as MCPResource


# Type alias for the reader function
ResourceReader = Callable[[str], Awaitable[list[str]]]


@dataclass
class ResourceInfo:
    """Information about an available resource with read capability.

    This class provides essential information about a resource and can read
    its content when a reader is available.
    """

    name: str
    """Name of the resource"""

    uri: str
    """URI identifying the resource location"""

    description: str | None = None
    """Optional description of the resource's content or purpose"""

    mime_type: str | None = None
    """MIME type of the resource content"""

    client: str | None = None
    """Name of the MCP client/server providing this resource"""

    annotations: dict[str, Any] = field(default_factory=dict)
    """Additional annotations/metadata for the resource"""

    _reader: ResourceReader | None = field(default=None, repr=False, compare=False)
    """Internal reader function for fetching content"""

    async def read(self) -> list[str]:
        """Read the resource content.

        Returns:
            List of text contents from the resource

        Raises:
            RuntimeError: If no reader is available or read fails
        """
        if self._reader is None:
            raise RuntimeError(f"No reader available for resource: {self.uri}")
        return await self._reader(self.uri)

    @classmethod
    async def from_mcp_resource(
        cls,
        resource: MCPResource,
        client_name: str | None = None,
        reader: ResourceReader | None = None,
    ) -> Self:
        """Create ResourceInfo from MCP resource.

        Args:
            resource: MCP resource object
            client_name: Name of the MCP client providing this resource
            reader: Optional reader function for fetching content

        Returns:
            ResourceInfo instance
        """
        annots = resource.annotations.model_dump(exclude_none=True) if resource.annotations else {}
        return cls(
            name=resource.name,
            uri=str(resource.uri),
            description=resource.description,
            mime_type=resource.mime_type,
            client=client_name,
            annotations=annots,
            _reader=reader,
        )
