"""T3 Code storage provider: read-only, over the environment's MCP endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from agentpool.agents.t3_agent import threads
from agentpool.log import get_logger
from agentpool_config.storage import T3StorageConfig
from agentpool_storage.base import StorageProvider
from agentpool_storage.models import ConversationData


if TYPE_CHECKING:
    from types import TracebackType

    from pyt3client import T3Client
    from pyt3client.models.tools import Thread, Thread1

    from agentpool.messaging import ChatMessage
    from agentpool.sessions import SessionData
    from agentpool_storage.models import QueryFilters


logger = get_logger(__name__)


class T3StorageProvider(StorageProvider):
    """The threads of a T3 Code environment as sessions (read-only).

    Lists threads (``t3_thread_list``) and reads their messages (``t3_thread_read``,
    the ``messages`` view) from a running environment. T3 keeps its threads itself, so
    writes are no-ops. A thread's ``cwd`` is its project's folder on the T3 host.
    """

    can_load_history: bool = True
    owns_data = False

    def __init__(
        self, config: T3StorageConfig | None = None, *, client: T3Client | None = None
    ) -> None:
        """Initialize the T3 Code storage provider.

        Args:
            config: T3 Code storage configuration
            client: A client to use (its owner manages it); else one signs in on enter
        """
        self.config: T3StorageConfig = config or T3StorageConfig()
        super().__init__(self.config)
        self._client = client
        self._owns_client = client is None
        self._project_id: str | None = None
        self._folders: dict[str, str] = {}

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        if self._client is None:
            token = self.config.token.get_secret_value() if self.config.token else None
            self._client = threads.connect(
                url=self.config.url,
                token=token,
                login=self.config.login,
                client_name="agentpool:storage",
            )
        client = self._client
        try:
            if self._owns_client:
                await client.__aenter__()
            if self.config.project is not None:
                self._project_id = await threads.resolve_project(client, self.config.project)
            self._folders = await threads.project_folders(client)
        except BaseException:
            if self._owns_client:
                await client.aclose()
                self._client = None
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
        await super().__aexit__(exc_type, exc_val, exc_tb)

    def _lists(self, agent_name: str | None) -> bool:
        """Whether a query for ``agent_name`` (None: any) covers the threads."""
        return agent_name is None or agent_name == self.config.agent_name

    async def get_sessions(self, filters: QueryFilters) -> list[ConversationData]:
        """The threads matching ``filters``, newest first, with their messages."""
        if self._client is None or not self._lists(filters.agent_name):
            return []
        scanning = any([filters.since, filters.query, filters.model, filters.cwd])
        try:
            listed = await threads.list_threads(
                self._client,
                project_id=self._project_id,
                limit=None if scanning else filters.limit,
            )
        except Exception:
            logger.exception("Failed to list T3 threads")
            return []
        result: list[ConversationData] = []
        for thread in listed:
            if not self._matches(thread, filters):
                continue
            messages = await self.get_session_messages(thread.thread_id)
            if filters.query and not any(filters.query in m.content for m in messages):
                continue
            result.append(
                ConversationData(
                    id=thread.thread_id,
                    agent=self.config.agent_name,
                    title=thread.title or None,
                    start_time=threads.timestamp(thread.created_at).isoformat(),
                    messages=list(messages),
                    token_usage=None,
                )
            )
            if filters.limit and len(result) >= filters.limit:
                break
        return result

    def _matches(self, thread: Thread, filters: QueryFilters) -> bool:
        if filters.since and threads.timestamp(thread.updated_at) < filters.since:
            return False
        if filters.model and filters.model not in thread.model:
            return False
        return not (filters.cwd and self._folders.get(thread.project_id) != filters.cwd)

    async def get_session_messages(
        self,
        session_id: str,
        *,
        include_ancestors: bool = False,
    ) -> list[ChatMessage[Any]]:
        """The thread's user and assistant messages, oldest first.

        Args:
            session_id: The thread's id
            include_ancestors: Ignored (a forked thread holds its whole conversation)
        """
        if self._client is None:
            return []
        try:
            messages = await threads.read_messages(
                self._client,
                session_id,
                session_id=session_id,
                agent_name=self.config.agent_name,
            )
        except Exception:
            logger.exception("Failed to read T3 thread", thread_id=session_id)
            return []
        return list(messages)

    async def _thread(self, session_id: str) -> Thread1 | None:
        if self._client is None:
            return None
        try:
            return await threads.read_thread(self._client, session_id)
        except Exception:  # Not a thread of this environment
            logger.debug("No T3 thread", thread_id=session_id, exc_info=True)
            return None

    async def get_session_title(self, session_id: str) -> str | None:
        """The thread's title."""
        thread = await self._thread(session_id)
        return thread.title or None if thread is not None else None

    async def load_session(self, session_id: str) -> SessionData | None:
        """The thread as session data (its project's folder as ``cwd``)."""
        from agentpool.sessions import SessionData

        if (thread := await self._thread(session_id)) is None:
            return None
        return SessionData(
            session_id=thread.thread_id,
            agent_name=self.config.agent_name,
            cwd=self._folders.get(thread.project_id),
            sdk_session_id=thread.thread_id,
            created_at=threads.timestamp(thread.created_at),
            last_active=threads.timestamp(thread.updated_at),
            metadata={"title": thread.title},
        )

    async def list_session_ids(
        self,
        pool_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[str]:
        """The threads' ids, newest first."""
        if self._client is None or not self._lists(agent_name):
            return []
        listed = await threads.list_threads(self._client, project_id=self._project_id)
        return [thread.thread_id for thread in listed]
