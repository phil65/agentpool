"""Shared state of the OpenCode V2 server."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from agentpool.log import get_logger
from agentpool.sessions.models import SessionData
from agentpool.utils.time_utils import get_now
from agentpool_server.opencode_v2_server.bus import EventBus
from agentpool_server.opencode_v2_server.catalog import Catalog
from agentpool_server.opencode_v2_server.event_store import EventStore
from agentpool_server.opencode_v2_server.events import build_event, new_session_id
from agentpool_server.opencode_v2_server.history import replay_history
from agentpool_server.opencode_v2_server.interactions import (
    FormRegistry,
    PermissionRegistry,
    SessionInputProvider,
)
from agentpool_server.opencode_v2_server.location import ProjectRegistry, normalize_directory
from agentpool_server.opencode_v2_server.ptys import PtyRegistry
from agentpool_server.opencode_v2_server.runner import AgentBinder, SessionRunner
from agentpool_server.opencode_v2_server.session import V2Session, empty_tokens
from agentpool_server.opencode_v2_server.shells import ShellManager
from opencode_v2_sdk.models import (
    LocationPublicRef,
    LocationRef,
    ModelRef,
    PermissionRule,
    SessionCreated,
    SessionCreatedData,
    SessionDeleted,
    SessionDeletedData,
    SessionInfo,
    SessionInfoTime,
    SessionRenamed,
    SessionRenamedData,
    TokenUsageInfo,
)


if TYPE_CHECKING:
    from exxec.pty_manager import PtyManagerProtocol
    from fsspec.asyn import AsyncFileSystem
    from pydantic import BaseModel

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.delegation import AgentPool
    from agentpool.storage import StorageManager
    from agentpool.storage.manager import SessionMetadataGeneratedEvent


logger = get_logger(__name__)

PROTOCOL = "opencode-v2"
META_KEY = "opencode_v2"
"""Key in ``SessionData.metadata`` holding V2-specific session state."""
SESSION_VERSION = "2"


class SessionNotFoundError(LookupError):
    """The requested session does not exist."""


def _slug(title: str | None, session_id: str) -> str:
    base = "".join(c if c.isalnum() else "-" for c in (title or "").lower()).strip("-")
    return base[:48] or session_id.removeprefix("ses_")[:12]


@dataclass
class ServerState:
    """State shared by all routes of one server."""

    pool: AgentPool[Any]
    working_dir: str
    host: str = "127.0.0.1"
    port: int | None = None
    bus: EventBus = field(default_factory=EventBus)
    store: EventStore = field(default_factory=EventStore)
    projects: ProjectRegistry = field(default_factory=ProjectRegistry)
    start_time: float = field(default_factory=time.time)
    sessions: dict[str, V2Session] = field(default_factory=dict)
    runners: dict[str, SessionRunner] = field(default_factory=dict)
    _load_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self.working_dir = normalize_directory(self.working_dir)
        self.catalog = Catalog(pool=self.pool)
        self.binder = AgentBinder(catalog=self.catalog)
        self.permissions = PermissionRegistry(publish=self.bus.publish)
        self.forms = FormRegistry(publish=self.bus.publish)
        self.ptys = PtyRegistry(
            manager=self._pty_manager,
            publish=self.bus.publish,
        )
        self.shells = ShellManager(
            publish=lambda event, kind, _directory: self.bus.publish(event, kind)
        )

    # --- accessors -----------------------------------------------------------------------

    def _pty_manager(self) -> PtyManagerProtocol:
        """PTY manager of the main agent's execution environment."""
        return self.pool.main_agent.env.get_pty_manager()

    @property
    def storage(self) -> StorageManager:
        """The pool's storage manager."""
        return self.pool.storage

    @property
    def fs(self) -> AsyncFileSystem:
        """Filesystem of the main agent's execution environment."""
        return self.pool.main_agent.env.get_fs()

    def publish(self, event: BaseModel, event_type: str) -> None:
        """Publish a non-session event to all subscribers."""
        self.bus.publish(event, event_type)

    def publish_global[E: BaseModel](
        self, event_cls: type[E], data: BaseModel, directory: str | None = None
    ) -> E:
        """Build and publish a non-session event."""
        event = build_event(event_cls, data, directory=directory or self.working_dir)
        self.bus.publish(event, str(event_cls.model_fields["type"].default))
        return event

    def connect_signals(self) -> None:
        """Connect storage (title generation) and agent signals (events between turns)."""
        self.storage.metadata_generated.connect(self._on_metadata_generated)
        for agent in self.pool.all_agents.values():
            agent.background_event.connect(self._on_background_event)

    def disconnect_signals(self) -> None:
        """Disconnect storage and agent signals."""
        self.storage.metadata_generated.disconnect(self._on_metadata_generated)
        for agent in self.pool.all_agents.values():
            with contextlib.suppress(ValueError):  # Agents added after startup
                agent.background_event.disconnect(self._on_background_event)

    async def _on_background_event(self, background: BaseAgent.BackgroundEvent) -> None:
        """An agent event outside a turn goes to the session the agent is bound to."""
        if background.session_id and (runner := self.runners.get(background.session_id)):
            await runner.on_background_event(background.event)

    async def _on_metadata_generated(self, event: SessionMetadataGeneratedEvent) -> None:
        session = self.sessions.get(event.session_id)
        title = event.metadata.title
        if session is None or not title or session.info.title:
            return
        session.emit(SessionRenamed, SessionRenamedData(session_id=session.id, title=title))
        await self.save_session_data(session)

    # --- sessions ------------------------------------------------------------------------

    async def create_session(
        self,
        *,
        directory: str | None = None,
        session_id: str | None = None,
        parent_id: str | None = None,
        title: str | None = None,
        agent: str | None = None,
        model: ModelRef | None = None,
        metadata: dict[str, Any] | None = None,
        permissions: list[PermissionRule] | None = None,
    ) -> V2Session:
        """Create, persist and announce a new session.

        Raises:
            ValueError: If the agent is unknown or the ID is already taken
        """
        sid = session_id or new_session_id()
        if sid in self.sessions or await self.store.exists(sid):
            msg = f"Session {sid} already exists"
            raise ValueError(msg)
        parent = await self.get_session(parent_id) if parent_id else None
        location = normalize_directory(
            directory or (parent.directory if parent else self.working_dir)
        )
        agent_name = agent or (parent.info.agent if parent else None)
        agent_name = agent_name or self.catalog.default_agent_name()
        node = self.catalog.get_node(agent_name)
        if node is None:
            msg = f"Unknown agent {agent_name!r}"
            raise ValueError(msg)
        model_ref = model or await self.catalog.current_model_ref(node)
        project = await self.projects.resolve(location)
        merged_metadata = (
            {**(parent.info.metadata or {}), **(metadata or {})} if parent else metadata
        )
        data = SessionCreatedData(
            session_id=sid,
            project_id=project.id,
            location=LocationRef(directory=location),
            parent_id=parent_id,
            slug=_slug(title, sid),
            title=title,
            agent=agent_name,
            model=model_ref,
            metadata=merged_metadata,
            permissions=permissions,
            version=SESSION_VERSION,
        )
        created = build_event(SessionCreated, data, aggregate_id=sid, seq=1, directory=location)
        session = V2Session.from_events([created], bus=self.bus, store=self.store)
        await self.store.write(sid, [created])
        self.sessions[sid] = session
        await self.save_session_data(session)
        self.bus.publish(created, "session.created")
        return session

    async def get_session(self, session_id: str) -> V2Session:
        """Get a session from the cache, its event log or the conversation storage.

        Raises:
            SessionNotFoundError: If the session does not exist
        """
        if (session := self.sessions.get(session_id)) is not None:
            return session
        async with self._load_lock:
            if (session := self.sessions.get(session_id)) is not None:
                return session
            session = await self._load_session(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            self.sessions[session_id] = session
            return session

    async def find_session(self, session_id: str) -> V2Session | None:
        """Like :meth:`get_session`, but returns None for unknown sessions."""
        try:
            return await self.get_session(session_id)
        except SessionNotFoundError:
            return None

    async def _load_session(self, session_id: str) -> V2Session | None:
        if not session_id.startswith("ses"):
            return None
        events = await self.store.load(session_id)
        if events:
            try:
                return V2Session.from_events(events, bus=self.bus, store=self.store)
            except ValueError:
                logger.warning("Corrupt session log, rebuilding", session_id=session_id)
        data = await self.storage.load_session(session_id)
        if data is None:
            return None
        return await self._import_session(data)

    async def _import_session(self, data: SessionData) -> V2Session:
        """Build a V2 session (and its event log) from stored session data and history."""
        directory = normalize_directory(data.cwd or self.working_dir)
        project = await self.projects.resolve(directory)
        node = self.catalog.get_node(data.agent_name)
        meta = self._v2_meta(data)
        model = self._stored_model(meta)
        if model is None and node is not None:
            model = await self.catalog.current_model_ref(node)
        created_ms = int(data.created_at.timestamp() * 1000)
        created = build_event(
            SessionCreated,
            SessionCreatedData(
                session_id=data.session_id,
                project_id=data.project_id or project.id,
                location=LocationRef(directory=directory),
                parent_id=data.parent_id if (data.parent_id or "").startswith("ses") else None,
                slug=_slug(data.title, data.session_id),
                title=data.title,
                agent=data.agent_name,
                model=model,
                version=SESSION_VERSION,
            ),
            aggregate_id=data.session_id,
            seq=1,
            created=created_ms,
            directory=directory,
        )
        session = V2Session.from_events([created], bus=self.bus, store=self.store)
        messages = await self.storage.get_session_messages(data.session_id)
        if model is not None:
            replay_history(session, messages, agent_name=data.agent_name, model_ref=model)
        await session.flush()
        await self.store.write(data.session_id, session.log)
        return session

    @staticmethod
    def _v2_meta(data: SessionData) -> dict[str, Any]:
        value = data.metadata.get(META_KEY)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _stored_model(meta: dict[str, Any]) -> ModelRef | None:
        raw = meta.get("model")
        if not isinstance(raw, dict):
            return None
        try:
            return ModelRef.model_validate(raw)
        except ValidationError:
            return None

    async def save_session_data(self, session: V2Session) -> None:
        """Persist the session summary to AgentPool's session storage."""
        info = session.info
        existing = await self.storage.load_session(info.id)
        meta = {
            "model": info.model.model_dump(by_alias=True, exclude_none=True)
            if info.model
            else None,
            "cost": info.cost,
            "tokens": info.tokens.model_dump(),
            "archived": info.time.archived,
            "metadata": info.metadata,
            "permissions": [p.model_dump() for p in info.permissions or []],
        }
        metadata = {
            **(existing.metadata if existing else {}),
            "protocol": PROTOCOL,
            META_KEY: meta,
        }
        if info.title:
            metadata["title"] = info.title
        data = SessionData(
            session_id=info.id,
            agent_name=info.agent or self.catalog.default_agent_name(),
            pool_id=self.pool.manifest.config_file_path,
            project_id=info.project_id,
            parent_id=info.parent_id,
            cwd=info.location.directory,
            agent_type=existing.agent_type if existing else None,
            sdk_session_id=existing.sdk_session_id if existing else None,
            definition_version=existing.definition_version if existing else None,
            created_at=existing.created_at if existing else get_now(),
            metadata=metadata,
        )
        await self.storage.save_session(data)

    def session_summary(self, data: SessionData) -> SessionInfo | None:
        """Session info built from stored session data (for sessions not loaded yet)."""
        if not data.session_id.startswith("ses"):
            return None
        meta = self._v2_meta(data)
        directory = normalize_directory(data.cwd or self.working_dir)
        created = int(data.created_at.timestamp() * 1000)
        updated = int(data.last_active.timestamp() * 1000)
        tokens = empty_tokens()
        if isinstance(raw_tokens := meta.get("tokens"), dict):
            try:
                tokens = TokenUsageInfo.model_validate(raw_tokens)
            except ValidationError:
                tokens = empty_tokens()
        archived = meta.get("archived")
        cost = meta.get("cost")
        return SessionInfo(
            id=data.session_id,
            parent_id=data.parent_id if (data.parent_id or "").startswith("ses") else None,
            project_id=data.project_id or "global",
            agent=data.agent_name,
            model=self._stored_model(meta),
            cost=float(cost) if isinstance(cost, int | float) else 0.0,
            tokens=tokens,
            time=SessionInfoTime(
                created=created,
                updated=updated,
                archived=archived if isinstance(archived, int | float) else None,
            ),
            title=data.title,
            location=LocationPublicRef(directory=directory),
        )

    async def list_sessions(self) -> list[SessionInfo]:
        """Info of all known sessions (loaded ones live, others from storage)."""
        infos: dict[str, SessionInfo] = {sid: s.info for sid, s in self.sessions.items()}
        for sid in await self.storage.list_session_ids():
            if sid in infos or not sid.startswith("ses"):
                continue
            data = await self.storage.load_session(sid)
            if data is not None and (summary := self.session_summary(data)) is not None:
                infos[sid] = summary
        return sorted(infos.values(), key=lambda i: i.time.updated, reverse=True)

    async def delete_session(self, session_id: str, *, _deleting: set[str] | None = None) -> None:
        """Delete a session, its children, history and event log.

        Raises:
            SessionNotFoundError: If the session does not exist
        """
        session = await self.get_session(session_id)
        deleting = _deleting if _deleting is not None else set()
        deleting.add(session_id)
        for info in await self.list_sessions():
            if info.parent_id == session_id and info.id not in deleting:
                await self.delete_session(info.id, _deleting=deleting)
        runner = self.runners.pop(session_id, None)
        if runner is not None:
            await runner.shutdown()
        self.permissions.cancel_session(session_id)
        self.forms.cancel_session(session_id)
        session.emit(SessionDeleted, SessionDeletedData(session_id=session_id))
        session.deleted = True
        session.close_followers()
        self.sessions.pop(session_id, None)
        self.pool.sessions.remove(session_id)
        await self.store.delete(session_id)
        await self.storage.delete_session(session_id)
        await self.storage.delete_session_messages(session_id)

    # --- runners -------------------------------------------------------------------------

    def runner(self, session: V2Session) -> SessionRunner:
        """The (lazily created) runner of a session."""
        if (runner := self.runners.get(session.id)) is not None:
            return runner
        provider = SessionInputProvider(
            session_id=session.id,
            project_id=session.info.project_id,
            directory=session.directory,
            permissions=self.permissions,
            forms=self.forms,
        )
        runner = SessionRunner(
            session=session,
            catalog=self.catalog,
            binder=self.binder,
            skills=self.pool.skills,
            input_provider=provider,
            on_turn_finished=self.save_session_data,
            compaction_pipeline=self.pool.compaction_pipeline,
        )
        provider.tool_message_id = runner.tool_message_id
        self.runners[session.id] = runner
        return runner

    def is_running(self, session_id: str) -> bool:
        """Whether a session currently executes a turn."""
        runner = self.runners.get(session_id)
        return runner is not None and runner.running

    async def shutdown(self) -> None:
        """Stop all runners and flush event logs."""
        for runner in list(self.runners.values()):
            await runner.shutdown()
        await self.shells.shutdown()
        await self.ptys.shutdown()
        for session in list(self.sessions.values()):
            session.close_followers()
            await session.flush()
        self.bus.close()
