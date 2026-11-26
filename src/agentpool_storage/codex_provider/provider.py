"""Codex storage provider - read-only session access via Codex protocol."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool_config.storage import CodexStorageConfig
from agentpool_storage.base import StorageProvider
from agentpool_storage.models import ConversationData


if TYPE_CHECKING:
    from codexed.client import CodexClient

    from agentpool.messaging import ChatMessage
    from agentpool_storage.models import QueryFilters


logger = get_logger(__name__)


class CodexStorageProvider(StorageProvider):
    """Read-only storage provider that queries a Codex server for sessions.

    This provider delegates session listing and message loading to a connected
    Codex server. All write operations are no-ops since the Codex server handles
    persistence internally.
    """

    can_load_history: bool = True
    owns_data = False

    def __init__(
        self,
        config: CodexStorageConfig | None = None,
        *,
        client: CodexClient | None = None,
        agent_name: str | None = None,
        cwd: str | None = None,
    ) -> None:
        """Initialize Codex storage provider.

        Args:
            config: Codex storage configuration
            client: Codex client (can be set later via set_client)
            agent_name: Name of the agent using this provider
            cwd: Working directory for session operations
        """
        super().__init__(config or CodexStorageConfig())
        self._client = client
        self._agent_name = agent_name
        self._cwd = cwd

    def set_client(self, client: CodexClient) -> None:
        """Set the Codex client after initialization.

        Args:
            client: Connected Codex client
        """
        self._client = client

    @property
    def is_connected(self) -> bool:
        """Whether the provider has an active client."""
        return self._client is not None

    async def get_sessions(self, filters: QueryFilters) -> list[ConversationData]:
        """Get sessions from the Codex server.

        Args:
            filters: Query filters to apply
        """
        if not self._client:
            return []

        try:
            response = await self._client.thread_list(limit=filters.limit)
        except Exception:
            logger.exception("Failed to list Codex threads")
            return []

        result: list[ConversationData] = []
        for thread_data in response.data:
            created_at = datetime.fromtimestamp(thread_data.created_at, tz=UTC)

            # Apply cwd filter
            thread_cwd = thread_data.cwd or self._cwd or ""
            if filters.cwd is not None and thread_cwd != filters.cwd:
                continue

            conv_data = ConversationData(
                id=thread_data.id,
                agent=self._agent_name or "codex",
                title=thread_data.preview or None,
                start_time=created_at.isoformat(),
                messages=[],
                token_usage=None,
            )
            result.append(conv_data)

        return result

    async def get_session_messages(
        self,
        session_id: str,
        *,
        include_ancestors: bool = False,
    ) -> list[ChatMessage[Any]]:
        """Load session messages from the Codex server.

        Resumes the thread to get the full conversation history.

        Args:
            session_id: Thread ID to load messages for
            include_ancestors: Ignored (Codex handles ancestry internally)
        """
        from agentpool.agents.codex_agent.codex_converters import turns_to_chat_messages

        if not self._client:
            return []

        try:
            session = await self._client.thread_resume(session_id)
        except Exception:
            logger.exception("Failed to load Codex thread", session_id=session_id)
            return []

        if not session.response.thread.turns:
            return []

        return turns_to_chat_messages(session.response.thread.turns)
