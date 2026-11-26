"""Zed IDE storage provider - reads from ~/.local/share/zed/threads format."""

from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Any

import anyenv
from pydantic_ai import RunUsage

from agentpool.log import get_logger
from agentpool.utils.time_utils import get_now, parse_iso_timestamp
from agentpool_config.storage import ZedStorageConfig
from agentpool_storage.base import StorageProvider
from agentpool_storage.models import ConversationData
from agentpool_storage.zed_provider import helpers
from agentpool_storage.zed_provider.models import ZedThread


if TYPE_CHECKING:
    from agentpool.messaging import ChatMessage
    from agentpool_config.session import SessionQuery
    from agentpool_storage.models import QueryFilters, StatsFilters

logger = get_logger(__name__)


class ZedStorageProvider(StorageProvider):
    """Storage provider that reads Zed IDE's native thread format.

    Zed stores conversations as zstd-compressed JSON in:
    - ~/.local/share/zed/threads/threads.db (SQLite)

    This is a READ-ONLY provider - it cannot write back to Zed's format.

    ## Supported Zed content types:
    - Text → str / TextPart
    - Image → BinaryContent
    - Mention (File, Directory, Symbol, Selection) → formatted str
    - Thinking → ThinkingPart
    - ToolUse → ToolCallPart
    - tool_results → ToolReturnPart

    ## Fields NOT available in Zed format:
    - Per-message token costs (only cumulative per thread)
    - Response time
    - Parent message ID (flat structure)
    - Forwarded from chain
    - Finish reason
    """

    can_load_history = True
    owns_data = False

    def __init__(self, config: ZedStorageConfig | None = None) -> None:
        """Initialize Zed storage provider.

        Args:
            config: Configuration for the provider
        """
        config = config or ZedStorageConfig()
        super().__init__(config)
        self.db_path = Path(config.path).expanduser()
        if not self.db_path.name.endswith(".db"):
            # If path is directory, add default db location
            self.db_path = self.db_path / "threads" / "threads.db"

    def _get_connection(self) -> closing[sqlite3.Connection]:
        """Open Zed's live database read-only; use as a context manager (closes it)."""
        if not self.db_path.exists():
            msg = f"Zed threads database not found: {self.db_path}"
            raise FileNotFoundError(msg)
        uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        return closing(sqlite3.connect(uri, uri=True))

    def _list_threads(
        self,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[tuple[str, str, str, str | None, str | None, str | None, str | None, str | None]]:
        """List threads with optional filtering.

        Args:
            since: Only return threads updated after this time
            limit: Maximum number of threads to return

        Returns:
            List of (id, summary, updated_at, created_at, parent_id,
            worktree_branch, folder_paths, folder_paths_order) tuples
        """
        try:
            with self._get_connection() as conn:
                query = (
                    "SELECT id, summary, updated_at, created_at, parent_id,"
                    " worktree_branch, folder_paths, folder_paths_order FROM threads"
                )
                params: list[Any] = []
                if since:
                    query += " WHERE updated_at >= ?"
                    params.append(since.isoformat())
                query += " ORDER BY updated_at DESC"
                if limit:
                    query += " LIMIT ?"
                    params.append(limit)
                cursor = conn.execute(query, params)
                threads = cursor.fetchall()
        except FileNotFoundError:
            return []
        except sqlite3.Error as e:
            logger.warning("Failed to list Zed threads", error=str(e))
            return []
        else:
            return threads

    def _load_thread(self, thread_id: str) -> ZedThread | None:
        """Load a single thread by ID."""
        try:
            with self._get_connection() as conn:
                query = "SELECT data_type, data FROM threads WHERE id = ? LIMIT 1"
                cursor = conn.execute(query, (thread_id,))
                row = cursor.fetchone()
            if row is None:
                return None

            data_type, data = row
            return ZedThread.from_compressed(data, data_type)
        except FileNotFoundError:
            return None
        except (sqlite3.Error, Exception) as e:  # noqa: BLE001
            logger.warning("Failed to load Zed thread", thread_id=thread_id, error=str(e))
            return None

    async def filter_messages(self, query: SessionQuery) -> list[ChatMessage[str]]:
        """Filter messages based on query."""
        messages: list[ChatMessage[str]] = []
        # Narrow thread list when a specific name is requested
        threads = self._list_threads()
        if query.name:
            threads = [t for t in threads if query.name in (t[0], t[1])]
        for thread_id, _summary, _updated_at, *_rest in threads:
            thread = self._load_thread(thread_id)
            if thread is None:
                continue
            for msg in helpers.thread_to_chat_messages(thread, thread_id):
                # Apply filters
                if query.agents and msg.name not in query.agents:
                    continue
                cutoff = query.get_time_cutoff()
                if query.since and cutoff and msg.timestamp and msg.timestamp < cutoff:
                    continue
                if query.until and msg.timestamp:
                    until_dt = datetime.fromisoformat(query.until)
                    if msg.timestamp > until_dt:
                        continue
                if query.contains and query.contains not in msg.content:
                    continue
                if query.roles and msg.role not in query.roles:
                    continue
                messages.append(msg)
                if query.limit and len(messages) >= query.limit:
                    return messages

        return messages

    async def log_message(self, *, message: ChatMessage[Any]) -> None:
        """Log a message - NOT SUPPORTED (read-only provider)."""
        logger.warning("ZedStorageProvider is read-only, cannot log messages")

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
        """Log a conversation - NOT SUPPORTED (read-only provider)."""
        logger.warning("ZedStorageProvider is read-only, cannot log conversations")

    async def get_sessions(self, filters: QueryFilters) -> list[ConversationData]:
        """Get filtered conversations with their messages."""
        result: list[ConversationData] = []
        # Use SQL-level filtering for efficiency
        for thread_id, summary, updated_at_str, created_at_str, *_rest in self._list_threads(
            since=filters.since
        ):
            updated_at = parse_iso_timestamp(updated_at_str)
            thread = self._load_thread(thread_id)
            if thread is None:
                continue
            messages = helpers.thread_to_chat_messages(thread, thread_id)
            if not messages:
                continue
            if filters.agent_name and not any(m.name == filters.agent_name for m in messages):
                continue
            if filters.query and not any(filters.query in m.content for m in messages):
                continue
            # Use created_at for start_time when available, fall back to updated_at
            start_time = parse_iso_timestamp(created_at_str) if created_at_str else updated_at
            conv_data = ConversationData(
                id=thread_id,
                agent="zed",
                title=summary or thread.title,
                start_time=start_time.isoformat(),
                messages=messages,
                token_usage=thread.cumulative_token_usage.to_run_usage(),
            )

            result.append(conv_data)
            if filters.limit and len(result) >= filters.limit:
                break

        return result

    async def get_session_stats(self, filters: StatsFilters) -> dict[str, dict[str, Any]]:
        """Get session statistics."""
        stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"usage": RunUsage(), "messages": 0, "models": set()}
        )
        # Use SQL-level filtering for efficiency
        for thread_id, _summary, updated_at_str, *_rest in self._list_threads(since=filters.cutoff):
            timestamp = parse_iso_timestamp(updated_at_str)
            thread = self._load_thread(thread_id)
            if thread is None:
                continue
            model = f"{thread.model.provider}:{thread.model.model}" if thread.model else "unknown"
            # Group by specified criterion
            match filters.group_by:
                case "model":
                    key = model
                case "hour":
                    key = timestamp.strftime("%Y-%m-%d %H:00")
                case "day":
                    key = timestamp.strftime("%Y-%m-%d")
                case _:
                    key = "zed"  # Default agent grouping

            stats[key]["messages"] += len(thread.messages)
            stats[key]["usage"] += thread.cumulative_token_usage.to_run_usage()
            stats[key]["models"].add(model)

        # Convert sets to lists for JSON serialization
        for value in stats.values():
            value["models"] = list(value["models"])

        return dict(stats)

    async def reset(self, *, agent_name: str | None = None, hard: bool = False) -> tuple[int, int]:
        """Reset storage - NOT SUPPORTED (read-only provider)."""
        logger.warning("ZedStorageProvider is read-only, cannot reset")
        return 0, 0

    async def get_session_counts(self, *, agent_name: str | None = None) -> tuple[int, int]:
        """Get counts of conversations and messages."""
        conv_count = 0
        msg_count = 0
        try:
            with self._get_connection() as conn:
                for data_type, data in conn.execute("SELECT data_type, data FROM threads"):
                    json_data = helpers.decompress(data, data_type)
                    thread_dict = anyenv.load_json(json_data, return_type=dict)
                    if (messages := thread_dict.get("messages")) is not None:
                        conv_count += 1
                        msg_count += len(messages)
        except FileNotFoundError:
            pass
        except sqlite3.Error as e:
            logger.warning("Failed to count Zed threads", error=str(e))
        return conv_count, msg_count

    async def get_session_title(self, session_id: str) -> str | None:
        """Get the title of a conversation."""
        return thread.title if (thread := self._load_thread(session_id)) else None

    async def get_session_messages(
        self,
        session_id: str,
        *,
        include_ancestors: bool = False,
    ) -> list[ChatMessage[str]]:
        """Get all messages for a session.

        Args:
            session_id: Thread ID (conversation ID in Zed format)
            include_ancestors: If True, traverse parent_id chain to include
                messages from ancestor conversations (not supported in Zed format)

        Returns:
            List of messages ordered by timestamp

        Note:
            Zed threads don't have parent_id chain, so include_ancestors has no effect.
        """
        if thread := self._load_thread(session_id):
            messages = helpers.thread_to_chat_messages(thread, session_id)
            # Sort by timestamp (though they should already be in order)
            now = get_now()
            messages.sort(key=lambda m: m.timestamp or now)
            return messages
        return []

    async def get_message(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> ChatMessage[str] | None:
        """Get a single message by ID.

        Args:
            message_id: ID of the message
            session_id: Optional session ID (thread_id) hint for faster lookup

        Returns:
            The message if found, None otherwise

        Note:
            Zed doesn't store individual message IDs, so this searches threads.
        """
        threads = [(session_id,)] if session_id else self._list_threads()
        for thread_id, *_rest in threads:
            if thread := self._load_thread(thread_id):
                for msg in helpers.thread_to_chat_messages(thread, thread_id):
                    if msg.message_id == message_id:
                        return msg
        return None

    async def get_message_ancestry(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> list[ChatMessage[str]]:
        """Get the ancestry chain of a message.

        Args:
            message_id: ID of the message
            session_id: Optional session ID (thread_id) hint for faster lookup

        Returns:
            List of messages from oldest ancestor to the specified message

        Note:
            Zed threads don't support parent_id chains, so this only returns
            the single message if found.
        """
        msg = await self.get_message(message_id, session_id=session_id)
        return [msg] if msg else []

    async def fork_conversation(
        self,
        *,
        source_session_id: str,
        new_session_id: str,
        fork_from_message_id: str | None = None,
        new_agent_name: str | None = None,
    ) -> str | None:
        """Fork a conversation at a specific point.

        Args:
            source_session_id: Source thread ID
            new_session_id: New thread ID
            fork_from_message_id: Message ID to fork from (not used - Zed is read-only)
            new_agent_name: Not used in Zed format

        Returns:
            None, as Zed storage is read-only

        Note:
            This is a READ-ONLY provider. Forking creates no persistent state.
            Returns None to indicate no fork point is available.
        """
        msg = "Fork conversation not supported for Zed storage (read-only)"
        logger.warning(msg, source=source_session_id, new=new_session_id)
        return None


if __name__ == "__main__":
    import asyncio
    import datetime as dt

    from agentpool_storage.models import QueryFilters, StatsFilters

    async def main() -> None:
        provider = ZedStorageProvider()
        print(f"Database: {provider.db_path}")
        print(f"Exists: {provider.db_path.exists()}")
        # List conversations
        filters = QueryFilters()
        conversations = await provider.get_sessions(filters)
        print(f"\nFound {len(conversations)} conversations")
        for conv_data in conversations[:5]:
            print(f"  - {conv_data['id'][:8]}... | {conv_data['title'] or 'Untitled'}")
            print(f"    Messages: {len(conv_data['messages'])}, Updated: {conv_data['start_time']}")
        # Get counts
        conv_count, msg_count = await provider.get_session_counts()
        print(f"\nTotal: {conv_count} conversations, {msg_count} messages")
        # Get stats
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
        stats_filters = StatsFilters(cutoff=cutoff, group_by="day")
        stats = await provider.get_session_stats(stats_filters)
        print(f"\nStats: {stats}")

    asyncio.run(main())
