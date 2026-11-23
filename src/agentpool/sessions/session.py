"""First-class Session runtime object owning conversation history and per-session overrides."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import Field
from schemez import Schema

from agentpool.messaging.message_history import MessageHistory
from agentpool.sessions.models import SessionData
from agentpool.tools.decisions import RememberedDecision
from agentpool.utils.identifiers import generate_session_id


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.messaging import ChatMessage
    from agentpool.sessions.activity import ActivityFeed
    from agentpool.storage import StorageManager


class SessionOverrides(Schema):
    """Per-session runtime overrides that survive resume without mutating shared definitions."""

    model: str | None = None
    """Model for this session's turns instead of the agent's (native agents)."""

    modes: dict[str, str] = Field(default_factory=dict)
    """Config option values for this session by option ID (native agents' options read
    them, e.g. ``mode`` for tool confirmation)."""

    tool_decisions: dict[str, RememberedDecision] = Field(default_factory=dict)
    """Decisions the user made for the whole session, by tool name (every agent type
    answers that tool's later calls with them instead of asking)."""

    # Session-scoped instructions and env vars belong here too, once the runtime can apply
    # them (ARCHITECTURE_SESSIONS_AGENTS.md section 10.9)

    def is_empty(self) -> bool:
        """Whether no overrides are set."""
        return self.model is None and not self.modes and not self.tool_decisions


class Session:
    """Live conversation state separated from agent definitions and runtimes.

    Owns the session's persisted metadata (:class:`SessionData`), conversation history
    (:class:`MessageHistory`), and per-session overrides (:class:`SessionOverrides`).
    """

    def __init__(
        self,
        data: SessionData,
        *,
        history: MessageHistory | None = None,
        overrides: SessionOverrides | None = None,
        storage: StorageManager | None = None,
    ) -> None:
        """Initialize a session.

        Args:
            data: Persistable session metadata
            history: Optional conversation history (created empty if omitted)
            overrides: Optional per-session overrides
            storage: Optional storage manager shared with history
        """
        self.data = data
        self.storage = storage
        self.history = history if history is not None else MessageHistory(storage=storage)
        self.overrides = overrides if overrides is not None else SessionOverrides()

    @classmethod
    def create(
        cls,
        agent_name: str,
        *,
        session_id: str | None = None,
        parent_id: str | None = None,
        cwd: str | None = None,
        agent_type: str | None = None,
        storage: StorageManager | None = None,
        messages: Sequence[ChatMessage[Any]] | None = None,
        overrides: SessionOverrides | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        """Create a new session with a generated or explicit session ID."""
        data = SessionData(
            session_id=session_id or generate_session_id(),
            agent_name=agent_name,
            parent_id=parent_id,
            cwd=cwd,
            agent_type=agent_type,
            metadata=dict(metadata or {}),
        )
        history = MessageHistory(storage=storage, messages=list(messages) if messages else None)
        return cls(data, history=history, overrides=overrides, storage=storage)

    @classmethod
    def from_session_data(
        cls,
        data: SessionData,
        *,
        history: MessageHistory | None = None,
        storage: StorageManager | None = None,
    ) -> Session:
        """Restore a session from persisted :class:`SessionData`."""
        raw_overrides = data.metadata.get("overrides")
        overrides = (
            SessionOverrides.model_validate(raw_overrides)
            if isinstance(raw_overrides, dict)
            else SessionOverrides()
        )
        return cls(data, history=history, overrides=overrides, storage=storage)

    @property
    def session_id(self) -> str:
        """Unique session identifier."""
        return self.data.session_id

    @property
    def parent_id(self) -> str | None:
        """Parent session ID if this session is a fork or child session."""
        return self.data.parent_id

    @property
    def agent_name(self) -> str:
        """Name of the agent currently bound to this session."""
        return self.data.agent_name

    @property
    def sdk_session_id(self) -> str | None:
        """External SDK session ID if backed by an external harness."""
        return self.data.sdk_session_id

    @property
    def cwd(self) -> str | None:
        """Working directory associated with this session."""
        return self.data.cwd

    @property
    def title(self) -> str | None:
        """Human-readable session title."""
        return self.data.title

    def set_session_id(self, session_id: str) -> None:
        """Update the session identifier (used when binding an externally assigned ID)."""
        if self.data.session_id != session_id:
            self.data = self.data.model_copy(update={"session_id": session_id})

    def set_sdk_session_id(self, sdk_session_id: str | None) -> None:
        """Record the external runtime's native session identifier."""
        if self.data.sdk_session_id != sdk_session_id:
            self.data = self.data.model_copy(update={"sdk_session_id": sdk_session_id})

    def rebind_agent(self, agent_name: str, *, agent_type: str | None = None) -> None:
        """Bind this session to a different agent."""
        updates: dict[str, Any] = {"agent_name": agent_name}
        if agent_type is not None:
            updates["agent_type"] = agent_type
        self.data = self.data.with_agent(agent_name).model_copy(update=updates)

    def fork(
        self,
        *,
        at_message_id: str | None = None,
        agent_name: str | None = None,
        session_id: str | None = None,
    ) -> Session:
        """Create a forked child session copying history up to ``at_message_id`` (or all).

        Args:
            at_message_id: Optional message ID to truncate the copied history at (inclusive).
                When ``None``, copies the entire history.
            agent_name: Optional agent name for the forked session (defaults to current).
            session_id: Optional explicit session ID for the fork.

        Raises:
            ValueError: If ``at_message_id`` is not found in this session's history.
        """
        source_messages = list(self.history.get_history())
        if at_message_id is not None:
            cutoff = next(
                (i for i, m in enumerate(source_messages) if m.message_id == at_message_id),
                None,
            )
            if cutoff is None:
                msg = f"Message {at_message_id!r} not found in session {self.session_id!r}"
                raise ValueError(msg)
            copied = source_messages[: cutoff + 1]
        else:
            copied = source_messages

        new_sid = session_id or generate_session_id()
        id_map = {m.message_id: str(uuid4()) for m in copied}
        retagged = [
            replace(
                m,
                session_id=new_sid,
                message_id=id_map[m.message_id],
                parent_id=id_map.get(m.parent_id, m.parent_id) if m.parent_id else None,
            )
            for m in copied
        ]
        return Session.create(
            agent_name=agent_name or self.agent_name,
            session_id=new_sid,
            parent_id=self.session_id,
            cwd=self.cwd,
            agent_type=self.data.agent_type if agent_name is None else None,
            storage=self.storage,
            messages=retagged,
            overrides=self.overrides.model_copy(deep=True),
            metadata={k: v for k, v in self.data.metadata.items() if k != "overrides"},
        )

    def child(
        self,
        agent_name: str,
        *,
        fork_history: bool = False,
        cwd: str | None = None,
        agent_type: str | None = None,
        session_id: str | None = None,
    ) -> Session:
        """Create a child session for subagent, worker, or team delegation.

        Args:
            agent_name: Agent bound to the child session
            fork_history: When ``True``, seed the child with a copy of this session's history
            cwd: Working directory override (defaults to this session's ``cwd``)
            agent_type: Optional agent type string
            session_id: Optional explicit session ID
        """
        if fork_history:
            forked = self.fork(agent_name=agent_name, session_id=session_id)
            updates: dict[str, Any] = {}
            if cwd is not None:
                updates["cwd"] = cwd
            if agent_type is not None:
                updates["agent_type"] = agent_type
            if updates:
                forked.data = forked.data.model_copy(update=updates)
            return forked
        return Session.create(
            agent_name=agent_name,
            session_id=session_id,
            parent_id=self.session_id,
            cwd=cwd if cwd is not None else self.cwd,
            agent_type=agent_type,
            storage=self.storage,
        )

    def snapshot(self) -> SessionData:
        """Return persistable :class:`SessionData` including any active overrides."""
        if self.overrides.is_empty():
            return self.data
        return self.data.with_metadata(overrides=self.overrides.model_dump(exclude_none=True))


MAX_SESSIONS = 512
"""Sessions a registry keeps in memory by default."""


class SessionRegistry:
    """In-memory registry of the live :class:`Session` objects in a pool.

    Bounded: beyond ``max_sessions`` the least recently used sessions are dropped.
    Servers keep the session of a running turn themselves and rebuild a missing one
    from storage, so dropping only costs a reload (and unsaved overrides).
    """

    def __init__(
        self,
        storage: StorageManager | None = None,
        *,
        max_sessions: int = MAX_SESSIONS,
        activity: ActivityFeed | None = None,
    ) -> None:
        self._storage = storage
        self._max_sessions = max_sessions
        self.activity = activity
        """Where new sessions are reported (the pool's activity feed)."""
        self._sessions: OrderedDict[str, Session] = OrderedDict()

    def register(self, session: Session) -> Session:
        """Register an existing session in the registry."""
        self._sessions[session.session_id] = session
        self._sessions.move_to_end(session.session_id)
        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)
        return session

    def create(
        self,
        agent_name: str,
        *,
        session_id: str | None = None,
        parent_id: str | None = None,
        cwd: str | None = None,
        agent_type: str | None = None,
        messages: Sequence[ChatMessage[Any]] | None = None,
        overrides: SessionOverrides | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        """Create and register a new session."""
        session = Session.create(
            agent_name=agent_name,
            session_id=session_id,
            parent_id=parent_id,
            cwd=cwd,
            agent_type=agent_type,
            storage=self._storage,
            messages=messages,
            overrides=overrides,
            metadata=metadata,
        )
        if self.activity is not None:
            self.activity.info(session.session_id, agent_name, parent_id=parent_id)
        return self.register(session)

    async def open(
        self,
        session_id: str,
        agent_name: str,
        *,
        parent_id: str | None = None,
        cwd: str | None = None,
        agent_type: str | None = None,
    ) -> Session:
        """The live session ``session_id``: registered, else restored from storage, else new.

        Restoring keeps what storage knows (``sdk_session_id``, the backend's own session to
        resume; title; metadata) and loads the history. ``agent_name`` binds a new or
        restored session; a registered one is re-bound if it changed.
        """
        if (session := self.get(session_id)) is not None:
            if session.agent_name != agent_name:
                session.rebind_agent(agent_name, agent_type=agent_type)
            return session
        stored = await self._storage.load_session(session_id) if self._storage else None
        if stored is None:
            return self.create(
                agent_name,
                session_id=session_id,
                parent_id=parent_id,
                cwd=cwd,
                agent_type=agent_type,
            )
        assert self._storage is not None
        messages = await self._storage.get_session_messages(session_id)
        history = MessageHistory(storage=self._storage, messages=messages or None)
        session = Session.from_session_data(stored, history=history, storage=self._storage)
        if session.agent_name != agent_name:
            session.rebind_agent(agent_name, agent_type=agent_type)
        return self.register(session)

    def get(self, session_id: str) -> Session | None:
        """Look up a registered session by ID."""
        if (session := self._sessions.get(session_id)) is not None:
            self._sessions.move_to_end(session_id)
        return session

    def fork(
        self,
        session_id: str,
        *,
        at_message_id: str | None = None,
        agent_name: str | None = None,
        new_session_id: str | None = None,
    ) -> Session:
        """Fork a registered session and register the resulting child."""
        parent = self._sessions.get(session_id)
        if parent is None:
            msg = f"Session {session_id!r} is not registered"
            raise KeyError(msg)
        child = parent.fork(
            at_message_id=at_message_id,
            agent_name=agent_name,
            session_id=new_session_id,
        )
        return self.register(child)

    def children_of(self, parent_id: str) -> list[Session]:
        """Return all registered sessions whose ``parent_id`` matches ``parent_id``."""
        return [s for s in self._sessions.values() if s.parent_id == parent_id]

    def list_sessions(self) -> list[Session]:
        """Return all registered sessions."""
        return list(self._sessions.values())

    def remove(self, session_id: str) -> Session | None:
        """Remove and return a session, with its child sessions, if present.

        Child sessions (subagent tasks) can only be continued from their parent.
        """
        for child in self.children_of(session_id):
            self.remove(child.session_id)
        return self._sessions.pop(session_id, None)

    def __contains__(self, session_id: object) -> bool:
        return isinstance(session_id, str) and session_id in self._sessions

    def __len__(self) -> int:
        return len(self._sessions)
