"""ACP storage provider - read-only session access via ACP protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentpool.log import get_logger
from agentpool.utils.time_utils import get_now, parse_iso_timestamp
from agentpool_config.storage import ACPStorageConfig
from agentpool_storage.base import StorageProvider
from agentpool_storage.models import ConversationData


if TYPE_CHECKING:
    from datetime import datetime

    from acp.client.connection import ClientSideConnection
    from agentpool_storage.models import QueryFilters


logger = get_logger(__name__)


class ACPStorageProvider(StorageProvider):
    """Read-only storage provider that queries an ACP server for sessions.

    This provider delegates session listing to a connected ACP server.
    Message loading is not supported at the provider level because ACP
    delivers messages via notifications during session load, which requires
    the agent's event loop.

    All write operations are no-ops since the ACP server handles
    persistence internally.
    """

    can_load_history: bool = True

    def __init__(
        self,
        config: ACPStorageConfig | None = None,
        *,
        connection: ClientSideConnection | None = None,
        agent_name: str | None = None,
        cwd: str | None = None,
    ) -> None:
        """Initialize ACP storage provider.

        Args:
            config: ACP storage configuration
            connection: ACP client connection (can be set later via set_connection)
            agent_name: Name of the agent using this provider
            cwd: Working directory for session operations
        """
        super().__init__(config or ACPStorageConfig())
        self._connection = connection
        self._agent_name = agent_name
        self._cwd = cwd

    def set_connection(self, connection: ClientSideConnection) -> None:
        """Set the ACP connection after initialization.

        Args:
            connection: Connected ACP client
        """
        self._connection = connection

    @property
    def is_connected(self) -> bool:
        """Whether the provider has an active connection."""
        return self._connection is not None

    async def get_sessions(
        self,
        filters: QueryFilters,
    ) -> list[ConversationData]:
        """Get sessions from the ACP server.

        Args:
            filters: Query filters to apply
        """
        from acp.schema import ListSessionsRequest

        if not self._connection:
            return []

        request = ListSessionsRequest(cwd=filters.cwd)
        try:
            response = await self._connection.list_sessions(request)
        except Exception:
            logger.exception("Failed to list sessions from ACP server")
            return []

        result: list[ConversationData] = []
        for acp_session in response.sessions:
            updated_at = self._parse_updated_at(acp_session.updated_at)
            meta = acp_session.field_meta or {}
            created_at = updated_at
            if (meta_created := meta.get("created_at")) and isinstance(meta_created, str):
                created_at = self._parse_updated_at(meta_created)

            conv_data = ConversationData(
                id=acp_session.session_id,
                agent=self._agent_name or "acp",
                title=acp_session.title,
                start_time=created_at.isoformat(),
                messages=[],
                token_usage=None,
            )
            result.append(conv_data)

        # Apply limit
        if filters.limit is not None:
            result = result[: filters.limit]
        return result

    def _parse_updated_at(self, date_str: str | None) -> datetime:
        """Parse a timestamp string, falling back to current time."""
        if not date_str:
            return get_now()
        return parse_iso_timestamp(date_str)
