"""Server-side state of one OpenCode V2 session.

A session is event-sourced, as in OpenCode: every change is a durable ``session.*``
event that gets a per-session sequence number, is appended to the persistent log and
is projected into the message list (:class:`SessionProjection`) and :class:`SessionInfo`.
Ephemeral events (deltas, progress, status) only go to live subscribers.
"""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_v2_server.events import (
    build_event,
    is_durable,
    is_durable_event,
)
from agentpool_server.opencode_v2_server.projector import SessionProjection
from opencode_v2_sdk.models import (
    LocationPublicRef,
    SessionAgentSelected,
    SessionArchived,
    SessionCreated,
    SessionExecutionFailed,
    SessionExecutionInterrupted,
    SessionExecutionStarted,
    SessionExecutionSucceeded,
    SessionForked,
    SessionInboxEnqueued,
    SessionInfo,
    SessionInfoFork,
    SessionInfoTime,
    SessionMetadataUpdated,
    SessionModelSelected,
    SessionMoved,
    SessionPermissions,
    SessionRenamed,
    SessionRevertCleared,
    SessionRevertCommitted,
    SessionRevertStaged,
    SessionStepEnded,
    SessionStepFailed,
    SessionStepStarted,
    SessionUnarchived,
    SessionUsageRecorded,
    SessionViewed,
    TokenUsageInfo,
    TokenUsageInfoCache,
)


if TYPE_CHECKING:
    from pydantic import BaseModel

    from agentpool_server.opencode_v2_server.bus import EventBus
    from agentpool_server.opencode_v2_server.event_store import EventStore
    from agentpool_server.opencode_v2_server.events import DurableSessionEvent


logger = get_logger(__name__)


def _add_tokens(a: TokenUsageInfo, b: TokenUsageInfo) -> TokenUsageInfo:
    return TokenUsageInfo(
        input=a.input + b.input,
        output=a.output + b.output,
        reasoning=a.reasoning + b.reasoning,
        cache=TokenUsageInfoCache(
            read=a.cache.read + b.cache.read, write=a.cache.write + b.cache.write
        ),
    )


def empty_tokens() -> TokenUsageInfo:
    """Zeroed token usage."""
    return TokenUsageInfo(
        input=0, output=0, reasoning=0, cache=TokenUsageInfoCache(read=0, write=0)
    )


def info_from_created(event: SessionCreated) -> SessionInfo:
    """Initial session info from its ``session.created`` event."""
    data = event.data
    created = event.created
    return SessionInfo(
        id=data.session_id,
        parent_id=data.parent_id,
        project_id=data.project_id,
        agent=data.agent,
        model=data.model,
        cost=0,
        tokens=empty_tokens(),
        time=SessionInfoTime(created=created, updated=created),
        title=data.title,
        subpath=data.subpath,
        metadata=data.metadata,
        permissions=data.permissions,
        location=LocationPublicRef(directory=data.location.directory),
    )


@dataclass
class V2Session:
    """Event-sourced state of one session."""

    info: SessionInfo
    bus: EventBus
    store: EventStore
    projection: SessionProjection
    log: list[DurableSessionEvent] = field(default_factory=list)
    seq: int = 0
    deleted: bool = False
    _followers: list[asyncio.Queue[DurableSessionEvent | None]] = field(default_factory=list)
    _unpersisted: list[DurableSessionEvent] = field(default_factory=list)
    _flush_task: asyncio.Task[None] | None = None
    replay_clock: int | None = None
    """When set (history replay), events are stamped with this time and not published."""

    @property
    def id(self) -> str:
        """The session ID."""
        return self.info.id

    @property
    def directory(self) -> str:
        """The session's location directory."""
        return self.info.location.directory

    # --- construction --------------------------------------------------------------------

    @classmethod
    def from_events(
        cls,
        events: list[DurableSessionEvent],
        *,
        bus: EventBus,
        store: EventStore,
    ) -> V2Session:
        """Rebuild a session by replaying its durable log.

        Raises:
            ValueError: If the log does not start with ``session.created``
        """
        if not events or not isinstance(created := events[0], SessionCreated):
            msg = "Session log must start with a session.created event"
            raise ValueError(msg)
        projection = SessionProjection(
            session_id=created.data.session_id,
            location=LocationPublicRef(directory=created.data.location.directory),
        )
        session = cls(info=info_from_created(created), bus=bus, store=store, projection=projection)
        for event in events:
            session._apply(event)
            session.log.append(event)
            session.seq = max(session.seq, event.durable.seq)
        return session

    # --- emitting ------------------------------------------------------------------------

    def emit[E: BaseModel](
        self,
        event_cls: type[E],
        data: BaseModel,
        *,
        metadata: dict[str, Any] | None = None,
        created: int | None = None,
    ) -> E:
        """Create, apply, persist and publish a session event.

        Args:
            event_cls: Generated event class
            data: Event payload
            metadata: Optional event metadata
            created: Optional creation time (epoch ms), defaults to now

        Returns:
            The emitted event.
        """
        durable = is_durable(event_cls)
        if durable:
            self.seq += 1
        event = build_event(
            event_cls,
            data,
            aggregate_id=self.id if durable else None,
            seq=self.seq if durable else None,
            created=created if created is not None else self.replay_clock,
            metadata=metadata,
            directory=self.directory,
        )
        if is_durable_event(event):
            self._apply(event)
            self.log.append(event)
            self._persist(event)
            for follower in self._followers:
                follower.put_nowait(event)
        if self.replay_clock is None:
            self.bus.publish(event, str(event_cls.model_fields["type"].default))
        return event

    def ingest(self, events: list[DurableSessionEvent]) -> None:
        """Append already built durable events (e.g. copied from another session).

        The events are applied and persisted but not published (they are history).
        """
        for event in events:
            self._apply(event)
            self.log.append(event)
            self.seq = max(self.seq, event.durable.seq)
            self._persist(event)

    def emit_sink(self, event_cls: type[BaseModel], data: BaseModel) -> None:
        """``EventSink``-compatible variant of :meth:`emit`."""
        self.emit(event_cls, data)

    def _apply(self, event: DurableSessionEvent) -> None:
        self.projection.apply(event)
        self._update_info(event)

    def _update_info(self, event: DurableSessionEvent) -> None:  # noqa: PLR0915
        info = self.info
        time = info.time
        created = event.created
        match event:
            case SessionCreated():
                return
            case SessionAgentSelected(data=data):
                info = info.model_copy(update={"agent": data.agent})
                time = time.model_copy(update={"updated": created})
            case SessionModelSelected(data=data):
                info = info.model_copy(update={"model": data.model})
                time = time.model_copy(update={"updated": created})
            case SessionRenamed(data=data):
                info = info.model_copy(update={"title": data.title})
                time = time.model_copy(update={"updated": created})
            case SessionMetadataUpdated(data=data):
                info = info.model_copy(update={"metadata": data.metadata})
                time = time.model_copy(update={"updated": created})
            case SessionPermissions(data=data):
                info = info.model_copy(update={"permissions": data.permissions})
                time = time.model_copy(update={"updated": created})
            case SessionMoved(data=data):
                info = info.model_copy(
                    update={
                        "project_id": data.project_id,
                        "subpath": data.subpath,
                        "location": LocationPublicRef(directory=data.location.directory),
                    }
                )
                time = time.model_copy(update={"updated": created})
            case SessionViewed(data=data):
                viewed = max(data.idle, time.viewed or data.idle)
                time = time.model_copy(update={"viewed": viewed})
            case SessionArchived():
                time = time.model_copy(update={"archived": created, "updated": created})
            case SessionUnarchived():
                time = time.model_copy(update={"archived": None, "updated": created})
            case SessionExecutionStarted():
                time = time.model_copy(update={"busy": created})
            case SessionExecutionSucceeded():
                info = info.model_copy(update={"outcome": "succeeded"})
                time = time.model_copy(update={"idle": created, "updated": created})
            case SessionExecutionFailed():
                info = info.model_copy(update={"outcome": "failed"})
                time = time.model_copy(update={"idle": created, "updated": created})
            case SessionExecutionInterrupted(data=data):
                if data.reason != "shutdown":
                    info = info.model_copy(update={"outcome": "interrupted"})
                    time = time.model_copy(update={"idle": created, "updated": created})
            case SessionForked(data=data):
                fork = SessionInfoFork(session_id=data.parent_id, boundary=data.boundary)
                info = info.model_copy(update={"fork": fork})
            case SessionRevertStaged(data=data):
                info = info.model_copy(update={"revert": data.revert})
                time = time.model_copy(update={"updated": created})
            case SessionRevertCleared() | SessionRevertCommitted():
                info = info.model_copy(update={"revert": None})
                time = time.model_copy(update={"updated": created})
            case SessionInboxEnqueued() | SessionStepStarted():
                time = time.model_copy(update={"updated": created})
            case SessionStepEnded(data=data):
                info = info.model_copy(
                    update={
                        "cost": info.cost + data.cost,
                        "tokens": _add_tokens(info.tokens, data.tokens),
                    }
                )
            case SessionStepFailed(data=data):
                if data.cost is not None and data.tokens is not None:
                    info = info.model_copy(
                        update={
                            "cost": info.cost + data.cost,
                            "tokens": _add_tokens(info.tokens, data.tokens),
                        }
                    )
            case SessionUsageRecorded(data=data):
                info = info.model_copy(
                    update={
                        "cost": info.cost + data.cost,
                        "tokens": _add_tokens(info.tokens, data.tokens),
                    }
                )
            case _:
                return
        self.info = info.model_copy(update={"time": time})

    # --- persistence ---------------------------------------------------------------------

    def _persist(self, event: DurableSessionEvent) -> None:
        if self.deleted:
            return
        self._unpersisted.append(event)
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush(), name=f"v2-log-{self.id}")

    async def _flush(self) -> None:
        while self._unpersisted and not self.deleted:
            batch = list(self._unpersisted)
            self._unpersisted.clear()
            try:
                await self.store.append(self.id, batch)
            except OSError:
                logger.exception("Failed to persist session events", session_id=self.id)

    async def flush(self) -> None:
        """Wait until all emitted durable events are persisted."""
        while self._flush_task is not None and not self._flush_task.done():
            await self._flush_task
        if self._unpersisted:
            await self._flush()

    # --- log following -------------------------------------------------------------------

    def events_after(self, seq: int) -> list[DurableSessionEvent]:
        """Durable events with a sequence number greater than ``seq``."""
        return [e for e in self.log if e.durable.seq > seq]

    def follow(self) -> asyncio.Queue[DurableSessionEvent | None]:
        """Register a follower queue receiving new durable events (None ends the stream)."""
        queue: asyncio.Queue[DurableSessionEvent | None] = asyncio.Queue()
        self._followers.append(queue)
        return queue

    def unfollow(self, queue: asyncio.Queue[DurableSessionEvent | None]) -> None:
        """Remove a follower queue."""
        if queue in self._followers:
            self._followers.remove(queue)

    def close_followers(self) -> None:
        """End all follower streams."""
        for queue in self._followers:
            queue.put_nowait(None)
        self._followers.clear()

    def touch(self) -> None:
        """Bump the session's updated time without an event."""
        self.info = self.info.model_copy(
            update={"time": self.info.time.model_copy(update={"updated": now_ms()})}
        )
