"""Storage provider base class."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any, Self, assert_never

from pydantic_ai import RunUsage

from agentpool.utils.tasks import TaskManager


if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from types import TracebackType

    from agentpool.common_types import JsonValue
    from agentpool.messaging import ChatMessage
    from agentpool.sessions.models import ProjectData, SessionData
    from agentpool_config.session import SessionQuery
    from agentpool_config.storage import BaseStorageProviderConfig
    from agentpool_storage.models import ConversationData, GroupBy, QueryFilters, StatsFilters


class StoredMessage:
    """Base class for stored message data."""

    id: str
    session_id: str
    timestamp: datetime
    role: str
    content: str
    name: str | None = None
    model: str | None = None
    token_usage: dict[str, int] | None = None
    cost: float | None = None
    response_time: float | None = None


class StoredConversation:
    """Base class for stored conversation data."""

    id: str
    agent_name: str
    start_time: datetime
    total_tokens: int = 0
    total_cost: float = 0.0


class StorageProvider:
    """Base class for storage providers."""

    can_load_history: bool = False
    """Whether this provider supports loading history."""

    owns_data: bool = True
    """Whether agentpool owns the stored data.

    False for providers that read another tool's history (Claude Code, Codex, Zed,
    T3, ACP servers). ``StorageManager.reset`` never resets those.
    """

    def __init__(self, config: BaseStorageProviderConfig) -> None:
        super().__init__()
        self.config = config
        self.task_manager = TaskManager()
        self.log_messages = config.log_messages
        self.log_sessions = config.log_sessions
        self.log_commands = config.log_commands

    async def __aenter__(self) -> Self:
        """Initialize provider resources."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up provider resources."""
        self.cleanup()

    def cleanup(self) -> None:
        """Clean up resources."""

    def should_log_agent(self, agent_name: str) -> bool:
        """Check if this provider should log the given agent."""
        return self.config.agents is None or agent_name in self.config.agents

    async def filter_messages(self, query: SessionQuery) -> list[ChatMessage[Any]]:
        """Get messages matching query (if supported)."""
        msg = f"{self.__class__.__name__} does not support loading history"
        raise NotImplementedError(msg)

    async def log_message(self, *, message: ChatMessage[Any]) -> None:
        """Log a message (if supported)."""

    async def log_session(
        self,
        *,
        session_id: str,
        node_name: str,
        start_time: datetime | None = None,
        model: str | None = None,
        agent_type: str | None = None,
        definition_version: str | None = None,
    ) -> None:
        """Log a conversation (if supported)."""

    async def update_session_title(self, session_id: str, title: str) -> None:
        """Update the title of a conversation.

        Args:
            session_id: ID of the conversation to update
            title: New title for the conversation
        """

    async def get_session_title(self, session_id: str) -> str | None:
        """Get the title of a conversation.

        Args:
            session_id: ID of the conversation

        Returns:
            The conversation title, or None if not set or conversation doesn't exist.
        """
        return None

    async def get_session_messages(
        self,
        session_id: str,
        *,
        include_ancestors: bool = False,
    ) -> list[ChatMessage[Any]]:
        """Get all messages for a session.

        Args:
            session_id: ID of the conversation
            include_ancestors: If True, also include messages from ancestor
                conversations (following parent_id chain). Useful for forked
                conversations where you want the full history.

        Returns:
            List of messages ordered by timestamp.
        """
        msg = f"{self.__class__.__name__} does not support getting conversation messages"
        raise NotImplementedError(msg)

    async def get_message(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> ChatMessage[Any] | None:
        """Get a single message by ID.

        Args:
            message_id: ID of the message
            session_id: Optional session ID hint for faster lookup

        Returns:
            The message if found, None otherwise.
        """
        return None

    async def get_message_ancestry(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> list[ChatMessage[Any]]:
        """Get the ancestry chain of a message.

        Traverses the parent_id chain to build full history leading to this message.
        Useful for forked conversations where you need context from the fork point.

        Args:
            message_id: ID of the message to get ancestry for
            session_id: Optional session ID hint for faster lookup

        Returns:
            List of messages from oldest ancestor to the specified message.
        """
        msg = f"{self.__class__.__name__} does not support message ancestry"
        raise NotImplementedError(msg)

    async def fork_conversation(
        self,
        *,
        source_session_id: str,
        new_session_id: str,
        fork_from_message_id: str | None = None,
        new_agent_name: str | None = None,
    ) -> str | None:
        """Fork a conversation at a specific point.

        Creates a new conversation that branches from the source conversation.
        The new conversation's first message will have parent_id pointing to
        the fork point, allowing history traversal.

        Args:
            source_session_id: ID of the conversation to fork from
            new_session_id: ID for the new forked conversation
            fork_from_message_id: Message ID to fork from. If None, forks from
                the last message in the source conversation.
            new_agent_name: Agent name for the new conversation. If None,
                inherits from source.

        Returns:
            The message_id of the fork point (the parent for new messages),
            or None if the source conversation is empty.
        """
        msg = f"{self.__class__.__name__} does not support forking conversations"
        raise NotImplementedError(msg)

    async def log_command(
        self,
        *,
        agent_name: str,
        session_id: str,
        command: str,
        context_type: type | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        """Log a command (if supported)."""

    async def get_commands(
        self,
        agent_name: str,
        session_id: str,
        *,
        limit: int | None = None,
        current_session_only: bool = False,
    ) -> list[str]:
        """Get command history (if supported)."""
        msg = f"{self.__class__.__name__} does not support retrieving commands"
        raise NotImplementedError(msg)

    async def get_sessions(self, filters: QueryFilters) -> list[ConversationData]:
        """Get filtered conversations with their messages.

        Args:
            filters: Query filters to apply
        """
        msg = f"{self.__class__.__name__} does not support conversation queries"
        raise NotImplementedError(msg)

    async def get_filtered_conversations(
        self,
        agent_name: str | None = None,
        period: str | None = None,
        since: datetime | None = None,
        query: str | None = None,
        model: str | None = None,
        limit: int | None = None,
        *,
        compact: bool = False,
        include_tokens: bool = False,
    ) -> list[ConversationData]:
        """Get filtered conversations with formatted output.

        Args:
            agent_name: Filter by agent name
            period: Time period to include (e.g. "1h", "2d")
            since: Only show conversations after this time
            query: Search in message content
            model: Filter by model used
            limit: Maximum number of conversations
            compact: Only show first/last message of each conversation
            include_tokens: Include token usage statistics
        """
        msg = f"{self.__class__.__name__} does not support filtered conversations"
        raise NotImplementedError(msg)

    async def get_session_stats(self, filters: StatsFilters) -> dict[str, dict[str, Any]]:
        """Get conversation statistics grouped by specified criterion.

        Args:
            filters: Filters for statistics query
        """
        msg = f"{self.__class__.__name__} does not support statistics"
        raise NotImplementedError(msg)

    def aggregate_stats(
        self,
        rows: Sequence[tuple[str | None, str | None, datetime, RunUsage]],
        group_by: GroupBy,
    ) -> dict[str, dict[str, Any]]:
        """Aggregate statistics data by specified grouping.

        Args:
            rows: Raw stats data (model, agent, timestamp, token_usage)
            group_by: How to group the statistics
        """
        stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"usage": RunUsage(), "messages": 0, "models": set()}
        )

        for model, agent, timestamp, token_usage in rows:
            match group_by:
                case "agent":
                    key = agent or "unknown"
                case "model":
                    key = model or "unknown"
                case "hour":
                    key = timestamp.strftime("%Y-%m-%d %H:00")
                case "day":
                    key = timestamp.strftime("%Y-%m-%d")
                case _ as unreachable:
                    assert_never(unreachable)

            entry = stats[key]
            entry["messages"] += 1
            entry["usage"] += token_usage
            if model:
                entry["models"].add(model)

        return stats

    async def reset(
        self,
        *,
        agent_name: str | None = None,
        hard: bool = False,
    ) -> tuple[int, int]:
        """Reset storage, optionally for specific agent only.

        Args:
            agent_name: Only reset data for this agent
            hard: Whether to completely reset storage (e.g., recreate tables)

        Returns:
            Tuple of (conversations deleted, messages deleted)
        """
        raise NotImplementedError

    async def get_session_counts(self, *, agent_name: str | None = None) -> tuple[int, int]:
        """Get counts of conversations and messages.

        Args:
            agent_name: Only count data for this agent

        Returns:
            Tuple of (conversation count, message count)
        """
        raise NotImplementedError

    async def delete_session_messages(self, session_id: str) -> int:
        """Delete all messages for a session.

        Used for compaction - removes existing messages so they can be
        replaced with compacted versions.

        Args:
            session_id: ID of the conversation to clear

        Returns:
            Number of messages deleted
        """
        msg = f"{self.__class__.__name__} does not support deleting messages"
        raise NotImplementedError(msg)

    # Project methods

    async def save_project(self, project: ProjectData) -> None:
        """Save or update a project.

        Args:
            project: Project data to persist
        """
        msg = f"{self.__class__.__name__} does not support project storage"
        raise NotImplementedError(msg)

    async def get_project(self, project_id: str) -> ProjectData | None:
        """Get a project by ID.

        Args:
            project_id: Project identifier

        Returns:
            Project data if found, None otherwise
        """
        msg = f"{self.__class__.__name__} does not support project storage"
        raise NotImplementedError(msg)

    async def get_project_by_worktree(self, worktree: str) -> ProjectData | None:
        """Get a project by worktree path.

        Args:
            worktree: Absolute path to the project worktree

        Returns:
            Project data if found, None otherwise
        """
        msg = f"{self.__class__.__name__} does not support project storage"
        raise NotImplementedError(msg)

    async def get_project_by_name(self, name: str) -> ProjectData | None:
        """Get a project by friendly name.

        Args:
            name: Project name

        Returns:
            Project data if found, None otherwise
        """
        msg = f"{self.__class__.__name__} does not support project storage"
        raise NotImplementedError(msg)

    async def list_projects(self, limit: int | None = None) -> list[ProjectData]:
        """List all projects, ordered by last_active descending.

        Args:
            limit: Maximum number of projects to return

        Returns:
            List of project data objects
        """
        msg = f"{self.__class__.__name__} does not support project storage"
        raise NotImplementedError(msg)

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project.

        Args:
            project_id: Project identifier

        Returns:
            True if project was deleted, False if not found
        """
        msg = f"{self.__class__.__name__} does not support project storage"
        raise NotImplementedError(msg)

    async def touch_project(self, project_id: str) -> None:
        """Update project's last_active timestamp.

        Args:
            project_id: Project identifier
        """
        msg = f"{self.__class__.__name__} does not support project storage"
        raise NotImplementedError(msg)

    # Session data methods

    async def save_session(self, data: SessionData) -> None:
        """Save or update session data.

        Args:
            data: Session data to persist
        """

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load session data by ID.

        Args:
            session_id: Session identifier

        Returns:
            Session data if found, None otherwise
        """
        return None

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session.

        Args:
            session_id: Session identifier

        Returns:
            True if session was deleted, False if not found
        """
        return False

    async def list_session_ids(
        self,
        pool_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[str]:
        """List session IDs, optionally filtered.

        Args:
            pool_id: Filter by pool/manifest ID
            agent_name: Filter by agent name

        Returns:
            List of session IDs
        """
        return []

    async def update_sdk_session_id(
        self,
        session_id: str,
        sdk_session_id: str,
    ) -> None:
        """Update the external SDK session ID for a session.

        Args:
            session_id: Internal session identifier
            sdk_session_id: External SDK session ID (e.g. Claude JSONL stem, Codex thread ID)
        """
