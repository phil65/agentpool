"""SQLModel-based storage provider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from pydantic_ai import RunUsage
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, desc, select

from agentpool.log import get_logger
from agentpool.utils.time_utils import get_now, parse_time_period
from agentpool_config.storage import SQLStorageConfig
from agentpool_storage.base import StorageProvider
from agentpool_storage.models import QueryFilters
from agentpool_storage.sql_provider.models import (
    CommandHistory,
    Conversation,
    Message,
    Project,
)
from agentpool_storage.sql_provider.utils import (
    build_message_query,
    format_conversation,
    parse_model_info,
    to_chat_message,
)


if TYPE_CHECKING:
    from datetime import datetime
    from types import TracebackType

    from agentpool.common_types import JsonValue
    from agentpool.messaging import ChatMessage
    from agentpool.sessions.models import ProjectData, SessionData
    from agentpool_config.session import SessionQuery
    from agentpool_storage.models import ConversationData, StatsFilters


logger = get_logger(__name__)

DEFINITION_VERSION_KEY = "definition_version"
"""Key of ``SessionData.definition_version`` in a conversation's metadata (no own column)."""


class SQLModelProvider(StorageProvider):
    """Storage provider using SQLModel.

    Can work with any database supported by SQLAlchemy/SQLModel.
    Provides efficient SQL-based filtering and storage.
    """

    can_load_history = True

    def __init__(self, config: SQLStorageConfig | None = None) -> None:
        """Initialize provider with async database engine.

        Args:
            config: Configuration for provider
        """
        config = config or SQLStorageConfig()
        super().__init__(config)
        self.engine = config.get_engine()
        self.auto_migrate = config.auto_migration

    async def _init_database(self, auto_migrate: bool = True) -> None:
        """Initialize database tables and run migrations.

        Args:
            auto_migrate: Whether to automatically run Alembic migrations
        """
        from sqlmodel import SQLModel

        from agentpool_storage.sql_provider.utils import run_alembic_migrations

        if auto_migrate:
            # Run migrations first (handles schema changes for existing DBs)
            async with self.engine.begin() as conn:
                await conn.run_sync(run_alembic_migrations)

        # Always ensure all tables exist (creates new tables, no-op for existing)
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def __aenter__(self) -> Self:
        """Initialize async database resources."""
        await self._init_database(auto_migrate=self.auto_migrate)
        await super().__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up async database resources properly."""
        await self.engine.dispose()
        return await super().__aexit__(exc_type, exc_val, exc_tb)

    def cleanup(self) -> None:
        """Clean up database resources."""
        # For sync cleanup, just pass - proper cleanup happens in __aexit__

    async def filter_messages(self, query: SessionQuery) -> list[ChatMessage[str]]:
        """Filter messages using SQL queries."""
        async with AsyncSession(self.engine) as session:
            stmt = build_message_query(query)
            result = await session.execute(stmt)
            messages = result.scalars().all()
            return [to_chat_message(msg) for msg in messages]

    async def log_message(self, *, message: ChatMessage[Any]) -> None:
        """Log message to database."""
        from agentpool.storage.serialization import serialize_messages

        provider, model_name = parse_model_info(message.model_name)
        cost_info = message.cost_info
        usage = message.usage
        async with AsyncSession(self.engine) as session:
            msg = Message(
                session_id=message.session_id or "",
                id=message.message_id,
                parent_id=message.parent_id,
                content=str(message.content),
                role=message.role,
                name=message.name,
                model=message.model_name,
                model_provider=provider,
                model_name=model_name,
                response_time=message.response_time,
                total_tokens=usage.total_tokens,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost=float(cost_info.total_cost) if cost_info else None,
                provider_name=message.provider_name,
                provider_response_id=message.provider_response_id,
                messages=serialize_messages(message.messages),
                finish_reason=message.finish_reason,
                timestamp=get_now(),
                metadata_json=dict(message.metadata) or None,
            )
            session.add(msg)
            await session.commit()

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
        """Log conversation to database (idempotent)."""
        from sqlalchemy import select

        from agentpool_storage.sql_provider.models import Conversation

        async with AsyncSession(self.engine) as session:
            existing = await session.execute(
                select(Conversation.id).where(Conversation.id == session_id)  # type: ignore[call-overload]  # ty:ignore[no-matching-overload]
            )
            if existing.scalar_one_or_none() is not None:
                return
            now = start_time or get_now()
            convo = Conversation(
                id=session_id,
                agent_name=node_name,
                start_time=now,
                model=model,
                agent_type=agent_type,
                metadata_json=(
                    {DEFINITION_VERSION_KEY: definition_version} if definition_version else {}
                ),
            )
            session.add(convo)
            await session.commit()

    async def update_session_title(self, session_id: str, title: str) -> None:
        """Update the title of a conversation."""
        async with AsyncSession(self.engine) as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == session_id)
            )
            conversation = result.scalar_one_or_none()
            if conversation:
                conversation.title = title
                session.add(conversation)
                await session.commit()

    async def get_session_title(self, session_id: str) -> str | None:
        """Get the title of a conversation."""
        async with AsyncSession(self.engine) as session:
            result = await session.execute(
                select(Conversation.title).where(Conversation.id == session_id)
            )
            return result.scalar_one_or_none()

    async def get_session_messages(
        self,
        session_id: str,
        *,
        include_ancestors: bool = False,
    ) -> list[ChatMessage[str]]:
        """Get all messages for a session.

        Args:
            session_id: ID of the conversation
            include_ancestors: If True, traverse parent_id chain to include
                messages from ancestor conversations (for forked convos).

        Returns:
            List of messages ordered by timestamp.
        """
        async with AsyncSession(self.engine) as session:
            # Get messages for this conversation
            result = await session.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.timestamp.asc())  # type: ignore
            )
            messages = [to_chat_message(m) for m in result.scalars().all()]

            if not include_ancestors or not messages:
                return messages

            # Find the first message's parent_id to get ancestor chain
            first_msg = messages[0]
            if first_msg.parent_id:
                ancestors = await self.get_message_ancestry(
                    first_msg.parent_id, session_id=session_id
                )
                return ancestors + messages

            return messages

    async def get_message(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> ChatMessage[str] | None:
        """Get a single message by ID."""
        async with AsyncSession(self.engine) as session:
            result = await session.execute(select(Message).where(Message.id == message_id))
            msg = result.scalar_one_or_none()
            return to_chat_message(msg) if msg else None

    async def get_message_ancestry(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> list[ChatMessage[str]]:
        """Get the ancestry chain of a message.

        Traverses parent_id chain to build full history. Stops at a cycle.
        """
        ancestors: list[ChatMessage[str]] = []
        current_id: str | None = message_id
        seen: set[str] = set()

        async with AsyncSession(self.engine) as session:
            while current_id and current_id not in seen:
                seen.add(current_id)
                result = await session.execute(select(Message).where(Message.id == current_id))
                msg = result.scalar_one_or_none()
                if not msg:
                    break
                ancestors.append(to_chat_message(msg))
                current_id = msg.parent_id

        # Reverse to get oldest first
        ancestors.reverse()
        return ancestors

    async def fork_conversation(
        self,
        *,
        source_session_id: str,
        new_session_id: str,
        fork_from_message_id: str | None = None,
        new_agent_name: str | None = None,
    ) -> str | None:
        """Fork a conversation at a specific point.

        Creates a new conversation record. The fork point message_id is returned
        so callers can set it as parent_id for new messages.
        """
        async with AsyncSession(self.engine) as session:
            # Get source conversation
            result = await session.execute(
                select(Conversation).where(Conversation.id == source_session_id)
            )
            source_conv = result.scalar_one_or_none()
            if not source_conv:
                msg = f"Source conversation not found: {source_session_id}"
                raise ValueError(msg)

            # Determine fork point
            fork_point_id: str | None = None
            if fork_from_message_id:
                # Verify the message exists and belongs to the source conversation
                msg_result = await session.execute(
                    select(Message).where(
                        Message.id == fork_from_message_id,
                        Message.session_id == source_session_id,
                    )
                )
                if not msg_result.scalar_one_or_none():
                    raise ValueError(f"Message {fork_from_message_id} not found in conversation")
                fork_point_id = fork_from_message_id
            else:
                # Fork from the last message
                msg_result = await session.execute(
                    select(Message)
                    .where(Message.session_id == source_session_id)
                    .order_by(desc(Message.timestamp))
                    .limit(1)
                )
                if last_msg := msg_result.scalar_one_or_none():
                    fork_point_id = last_msg.id

            # Create new conversation
            agent_name = new_agent_name or source_conv.agent_name
            new_conv = Conversation(
                id=new_session_id,
                agent_name=agent_name,
                title=f"{source_conv.title or 'Conversation'} (fork)"
                if source_conv.title
                else None,
                start_time=get_now(),
            )
            session.add(new_conv)
            await session.commit()

            return fork_point_id

    async def log_command(
        self,
        *,
        agent_name: str,
        session_id: str,
        command: str,
        context_type: type | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        """Log command to database."""
        async with AsyncSession(self.engine) as session:
            history = CommandHistory(
                session_id=session_id,
                agent_name=agent_name,
                command=command,
                context_type=context_type.__name__ if context_type else None,
                context_metadata=metadata or {},
            )
            session.add(history)
            await session.commit()

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
        """Get filtered conversations with formatted output."""
        # Convert period to since if provided
        if period:
            since = get_now() - parse_time_period(period)

        # Create filters
        filters = QueryFilters(
            agent_name=agent_name,
            since=since,
            query=query,
            model=model,
            limit=limit,
        )

        # Use existing get_sessions method
        return [
            format_conversation(i, i["messages"], compact=compact, include_tokens=include_tokens)
            for i in await self.get_sessions(filters)
        ]

    async def get_commands(
        self,
        agent_name: str,
        session_id: str,
        *,
        limit: int | None = None,
        current_session_only: bool = False,
    ) -> list[str]:
        """Get command history from database."""
        async with AsyncSession(self.engine) as session:
            query = select(CommandHistory)
            if current_session_only:
                query = query.where(CommandHistory.session_id == str(session_id))
            else:
                query = query.where(CommandHistory.agent_name == agent_name)

            query = query.order_by(desc(CommandHistory.timestamp))
            if limit:
                query = query.limit(limit)

            result = await session.execute(query)
            return [h.command for h in result.scalars()]

    async def get_sessions(self, filters: QueryFilters) -> list[ConversationData]:
        """Get filtered conversations using SQL queries."""
        async with AsyncSession(self.engine) as session:
            results: list[ConversationData] = []
            # Base conversation query
            conv_query = select(Conversation)
            if filters.agent_name:
                conv_query = conv_query.where(Conversation.agent_name == filters.agent_name)
            # Apply time filters if provided
            if filters.since:
                conv_query = conv_query.where(Conversation.start_time >= filters.since)
            if filters.limit:
                conv_query = conv_query.limit(filters.limit)
            conv_result = await session.execute(conv_query)
            for conv in conv_result.scalars().all():
                # Get messages for this conversation
                msg_query = select(Message).where(Message.session_id == conv.id)

                if filters.query:
                    msg_query = msg_query.where(Message.content.contains(filters.query))  # type: ignore
                if filters.model:
                    msg_query = msg_query.where(Message.model_name == filters.model)

                msg_query = msg_query.order_by(Message.timestamp.asc())  # type: ignore
                msg_result = await session.execute(msg_query)
                messages = msg_result.scalars().all()

                if not messages:
                    continue
                chat_msgs = [to_chat_message(msg) for msg in messages]
                results.append(format_conversation(conv, chat_msgs))

            return results

    async def get_session_stats(self, filters: StatsFilters) -> dict[str, dict[str, Any]]:
        """Get statistics using SQL aggregations."""
        from agentpool_storage.sql_provider.models import Conversation, Message

        async with AsyncSession(self.engine) as session:
            # Base query for stats
            query = (
                select(  # type: ignore[call-overload]
                    Message.model,
                    Conversation.agent_name,
                    Message.timestamp,
                    Message.total_tokens,
                    Message.input_tokens,
                    Message.output_tokens,
                )  # ty:ignore[no-matching-overload]
                .join(Conversation, Message.session_id == Conversation.id)
                .where(Message.timestamp > filters.cutoff)
            )

            if filters.agent_name:
                query = query.where(Conversation.agent_name == filters.agent_name)

            # Execute query and get raw data
            result = await session.execute(query)
            rows = [
                (
                    model,
                    agent,
                    timestamp,
                    RunUsage(input_tokens=input_tokens, output_tokens=output_tokens),
                )
                for model, agent, timestamp, total, input_tokens, output_tokens in result.all()
            ]

        # Use base class aggregation
        return self.aggregate_stats(rows, filters.group_by)

    async def reset(self, *, agent_name: str | None = None, hard: bool = False) -> tuple[int, int]:
        """Reset database storage."""
        from sqlalchemy import text

        from agentpool_storage.sql_provider.queries import (
            DELETE_AGENT_CONVERSATIONS,
            DELETE_AGENT_MESSAGES,
            DELETE_ALL_CONVERSATIONS,
            DELETE_ALL_MESSAGES,
        )

        if hard:
            if agent_name:
                msg = "Hard reset cannot be used with agent_name"
                raise ValueError(msg)
            # Drop and recreate all tables
            async with self.engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.drop_all)
            await self._init_database()
            return 0, 0

        conv_count, msg_count = await self.get_session_counts(agent_name=agent_name)
        async with AsyncSession(self.engine) as session:
            if agent_name:
                await session.execute(text(DELETE_AGENT_MESSAGES), {"agent": agent_name})
                await session.execute(text(DELETE_AGENT_CONVERSATIONS), {"agent": agent_name})
            else:
                await session.execute(text(DELETE_ALL_MESSAGES))
                await session.execute(text(DELETE_ALL_CONVERSATIONS))
            await session.commit()
        return conv_count, msg_count

    async def get_session_counts(self, *, agent_name: str | None = None) -> tuple[int, int]:
        """Get conversation and message counts."""
        from sqlalchemy import func

        conv_query = select(func.count()).select_from(Conversation)
        msg_query = select(func.count()).select_from(Message)
        if agent_name:
            conv_query = conv_query.where(Conversation.agent_name == agent_name)
            msg_query = msg_query.join(Conversation, Message.session_id == Conversation.id).where(  # ty:ignore[invalid-argument-type]
                Conversation.agent_name == agent_name
            )
        async with AsyncSession(self.engine) as session:
            conv_count = (await session.execute(conv_query)).scalar() or 0
            msg_count = (await session.execute(msg_query)).scalar() or 0
        return conv_count, msg_count

    async def delete_session_messages(self, session_id: str) -> int:
        """Delete all messages for a session."""
        from sqlalchemy import delete, func

        async with AsyncSession(self.engine) as session:
            # First count messages to return
            count_result = await session.execute(
                select(func.count()).where(Message.session_id == session_id)
            )
            count = count_result.scalar() or 0
            # Then delete
            await session.execute(
                delete(Message).where(Message.session_id == session_id)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            )
            await session.commit()
            return count

    # Project methods

    def _to_project_data(self, row: Project) -> ProjectData:
        """Convert database model to ProjectData."""
        from agentpool.sessions.models import ProjectData

        return ProjectData(
            project_id=row.project_id,
            worktree=row.worktree,
            name=row.name,
            vcs=row.vcs,
            config_path=row.config_path,
            created_at=row.created_at,
            last_active=row.last_active,
            settings=row.settings_json or {},
        )

    def _to_project_model(self, data: ProjectData) -> Project:
        """Convert ProjectData to database model."""
        return Project(
            project_id=data.project_id,
            worktree=data.worktree,
            name=data.name,
            vcs=data.vcs,
            config_path=data.config_path,
            created_at=data.created_at,
            last_active=data.last_active,
            settings_json=data.settings,
        )

    async def save_project(self, project: ProjectData) -> None:
        """Save or update a project."""
        from sqlalchemy import delete

        async with AsyncSession(self.engine) as session:
            # Delete existing if present (upsert via delete+insert)
            stmt = delete(Project).where(Project.project_id == project.project_id)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            await session.execute(stmt)
            # Insert new/updated
            db_project = self._to_project_model(project)
            session.add(db_project)
            await session.commit()
            logger.debug("Saved project", project_id=project.project_id)

    async def get_project(self, project_id: str) -> ProjectData | None:
        """Get a project by ID."""
        async with AsyncSession(self.engine) as session:
            stmt = select(Project).where(Project.project_id == project_id)
            result = await session.execute(stmt)
            row = result.scalars().first()
            return self._to_project_data(row) if row else None

    async def get_project_by_worktree(self, worktree: str) -> ProjectData | None:
        """Get a project by worktree path."""
        async with AsyncSession(self.engine) as session:
            stmt = select(Project).where(Project.worktree == worktree)
            result = await session.execute(stmt)
            row = result.scalars().first()
            return self._to_project_data(row) if row else None

    async def get_project_by_name(self, name: str) -> ProjectData | None:
        """Get a project by friendly name."""
        async with AsyncSession(self.engine) as session:
            stmt = select(Project).where(Project.name == name)
            result = await session.execute(stmt)
            row = result.scalars().first()
            return self._to_project_data(row) if row else None

    async def list_projects(self, limit: int | None = None) -> list[ProjectData]:
        """List all projects, ordered by last_active descending."""
        async with AsyncSession(self.engine) as session:
            stmt = select(Project).order_by(desc(Project.last_active))
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return [self._to_project_data(row) for row in result.scalars().all()]

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project."""
        from sqlalchemy import delete

        async with AsyncSession(self.engine) as session:
            stmt = delete(Project).where(Project.project_id == project_id)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            result = await session.execute(stmt)
            await session.commit()
            deleted = isinstance(result, CursorResult) and result.rowcount > 0
            if deleted:
                logger.debug("Deleted project", project_id=project_id)
            return deleted

    async def touch_project(self, project_id: str) -> None:
        """Update project's last_active timestamp."""
        from sqlalchemy import update

        async with AsyncSession(self.engine) as session:
            stmt = (
                update(Project)
                .where(Project.project_id == project_id)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
                .values(last_active=get_now())
            )
            await session.execute(stmt)
            await session.commit()

    # Session data methods

    async def save_session(self, data: SessionData) -> None:
        """Save or update session data.

        Uses the Conversation table to store session lifecycle data alongside
        conversation tracking data. Updating an existing session only touches the
        columns owned by ``SessionData``: the creation time, token/cost totals and
        model are kept, and a missing title never clears a stored one.
        """
        async with AsyncSession(self.engine) as session:
            convo = await session.get(Conversation, data.session_id)
            if convo is None:
                convo = Conversation(
                    id=data.session_id,
                    agent_name=data.agent_name,
                    start_time=data.created_at,
                )
            convo.agent_name = data.agent_name
            convo.pool_id = data.pool_id
            convo.project_id = data.project_id
            convo.parent_id = data.parent_id
            convo.version = data.version
            convo.cwd = data.cwd
            convo.agent_type = data.agent_type
            convo.sdk_session_id = data.sdk_session_id
            convo.last_active = data.last_active
            # The title column is canonical; `load_session` re-adds it to the metadata.
            metadata = {k: v for k, v in data.metadata.items() if k != "title"}
            if data.definition_version is not None:
                metadata[DEFINITION_VERSION_KEY] = data.definition_version
            convo.metadata_json = metadata
            if data.title is not None:
                convo.title = data.title
            session.add(convo)
            await session.commit()
            logger.debug("Saved session", session_id=data.session_id)

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load session data by ID."""
        async with AsyncSession(self.engine) as session:
            row = await session.get(Conversation, session_id)
            return None if row is None else _session_data(row)

    async def list_sessions(
        self,
        *,
        agent_name: str | None = None,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """Sessions, most recently active first, with their titles: one query."""
        async with AsyncSession(self.engine) as session:
            stmt = select(Conversation)
            if agent_name is not None:
                stmt = stmt.where(Conversation.agent_name == agent_name)
            if cwd is not None:
                stmt = stmt.where(Conversation.cwd == cwd)
            stmt = stmt.order_by(Conversation.last_active.desc())  # type: ignore[attr-defined]  # ty:ignore[missing-attribute]
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return [_session_data(row) for row in result.scalars().all()]

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        from sqlalchemy import delete

        async with AsyncSession(self.engine) as session:
            stmt = delete(Conversation).where(Conversation.id == session_id)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            result = await session.execute(stmt)
            await session.commit()
            deleted = isinstance(result, CursorResult) and result.rowcount > 0
            if deleted:
                logger.debug("Deleted session", session_id=session_id)
            return deleted

    async def list_session_ids(
        self,
        pool_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[str]:
        """List session IDs, optionally filtered."""
        async with AsyncSession(self.engine) as session:
            stmt = select(Conversation.id)
            if pool_id is not None:
                stmt = stmt.where(Conversation.pool_id == pool_id)
            if agent_name is not None:
                stmt = stmt.where(Conversation.agent_name == agent_name)
            stmt = stmt.order_by(Conversation.last_active.desc())  # type: ignore[attr-defined]  # ty:ignore[missing-attribute]
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_sdk_session_id(
        self,
        session_id: str,
        sdk_session_id: str,
    ) -> None:
        """Update the external SDK session ID for a session."""
        from sqlalchemy import update

        async with AsyncSession(self.engine) as db:
            stmt = (
                update(Conversation)
                .where(Conversation.id == session_id)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
                .values(sdk_session_id=sdk_session_id)
            )
            await db.execute(stmt)
            await db.commit()


def _session_data(row: Conversation) -> SessionData:
    """The stored session of a ``Conversation`` row (its title in the metadata)."""
    from agentpool.sessions.models import SessionData

    metadata = dict(row.metadata_json or {})
    definition_version = metadata.pop(DEFINITION_VERSION_KEY, None)
    if row.title is not None:
        metadata["title"] = row.title
    return SessionData(
        session_id=row.id,
        agent_name=row.agent_name,
        pool_id=row.pool_id,
        project_id=row.project_id,
        parent_id=row.parent_id,
        version=row.version,
        cwd=row.cwd,
        agent_type=row.agent_type,
        sdk_session_id=row.sdk_session_id,
        definition_version=(definition_version if isinstance(definition_version, str) else None),
        created_at=row.start_time,
        last_active=row.last_active,
        metadata=metadata,
    )
