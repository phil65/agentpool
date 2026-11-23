"""The pool's activity feed: session lifecycle and foreground state, as ACP v2 reports them.

``state_update``: a session's foreground work started (``running``), waits for the user
(``requires_action``) or stopped (``idle``, with the stop reason and the turn's usage).
``info_update``: a session was created or got its title.
``usage_update``: the session's context fill and cumulative cost after a response.
``notice``: information for the user that is not part of the conversation.
``compaction_update``: a context compaction started, completed or failed.

Each entry has a sequence number, so a reader (``serve-mcp``'s published events) can ask for
what came after the last one it saw. The feed keeps the latest ``max_entries``: a reader that
fell further behind learns that it missed some (``truncated``).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from agentpool.agents.events import RichAgentStreamEvent

ActivityKind = Literal["state_update", "info_update", "usage_update", "notice", "compaction_update"]
SessionState = Literal["running", "idle", "requires_action"]
StopReason = Literal["end_turn", "cancelled", "error"]
CompactionStatus = Literal["in_progress", "completed", "failed"]

MAX_ENTRIES = 1000


@dataclass(frozen=True, kw_only=True)
class Activity:
    """One entry of the feed."""

    seq: int
    """Position in the feed (increasing, starting at 1)."""

    kind: ActivityKind
    session_id: str
    agent: str
    data: dict[str, Any]
    """The update: for ``state_update`` ``state`` (and for ``idle`` ``stop_reason``,
    ``usage``, ``error``); for ``info_update`` ``title``, ``parent_id``."""

    timestamp: datetime = field(default_factory=get_now)

    def to_payload(self) -> dict[str, Any]:
        """The entry as an event payload."""
        return {
            "session_id": self.session_id,
            "agent": self.agent,
            "timestamp": self.timestamp.isoformat(),
            **self.data,
        }


@dataclass(frozen=True)
class ActivitySlice:
    """Entries after a position."""

    entries: list[Activity]
    position: int
    """The feed's position when read (the next read continues after it)."""

    truncated: bool
    """Entries after the asked-for position were already dropped."""


class ActivityFeed:
    """Recent session lifecycle and foreground state of a pool's agents."""

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: deque[Activity] = deque(maxlen=max_entries)
        self._position = 0
        self._compactions: dict[str, str] = {}
        """ID of the compaction in progress, per session."""

    def __repr__(self) -> str:
        return f"ActivityFeed(position={self._position}, kept={len(self._entries)})"

    @property
    def position(self) -> int:
        """Sequence number of the latest entry (0: none yet)."""
        return self._position

    def _add(self, kind: ActivityKind, session_id: str, agent: str, data: dict[str, Any]) -> None:
        self._position += 1
        entry = Activity(
            seq=self._position, kind=kind, session_id=session_id, agent=agent, data=data
        )
        self._entries.append(entry)

    def state(
        self,
        session_id: str,
        agent: str,
        state: SessionState,
        *,
        stop_reason: StopReason | None = None,
        usage: dict[str, int] | None = None,
        error: str | None = None,
    ) -> None:
        """Record a change of a session's foreground state."""
        data: dict[str, Any] = {"state": state}
        if state == "idle":
            data["stop_reason"] = stop_reason
            if usage is not None:
                data["usage"] = usage
            if error is not None:
                data["error"] = error
        self._add("state_update", session_id, agent, data)

    def info(
        self,
        session_id: str,
        agent: str,
        *,
        title: str | None = None,
        parent_id: str | None = None,
    ) -> None:
        """Record a session's creation or a change of its metadata."""
        self._add("info_update", session_id, agent, {"title": title, "parent_id": parent_id})

    def usage(
        self,
        session_id: str,
        agent: str,
        *,
        used: int | None,
        size: int | None,
        cost: float | None,
    ) -> None:
        """Record the session's context fill (tokens ``used`` of ``size``) and cost so far."""
        data = {"used": used, "size": size, "cost": cost, "currency": "USD"}
        self._add("usage_update", session_id, agent, data)

    def notice(
        self,
        session_id: str,
        agent: str,
        *,
        severity: str,
        title: str,
        description: str | None = None,
    ) -> None:
        """Record information for the user that is not part of the conversation."""
        data = {"severity": severity, "title": title, "description": description}
        self._add("notice", session_id, agent, data)

    def compaction(
        self,
        session_id: str,
        agent: str,
        *,
        status: CompactionStatus,
        trigger: str | None = None,
        pre_tokens: int | None = None,
        error: str | None = None,
    ) -> None:
        """Record a step of a context compaction (one ``compaction_id`` from start to end)."""
        if status == "in_progress" or session_id not in self._compactions:
            self._compactions[session_id] = uuid4().hex
        compaction_id = self._compactions[session_id]
        if status != "in_progress":
            del self._compactions[session_id]
        data = {
            "compaction_id": compaction_id,
            "status": status,
            "trigger": trigger,
            "pre_tokens": pre_tokens,
            "error": error,
        }
        self._add("compaction_update", session_id, agent, data)

    def observe(self, session_id: str, agent: str, event: RichAgentStreamEvent[Any]) -> None:
        """Record what an agent's stream event says about its session (notices, compactions)."""
        from agentpool.agents.events import CompactionEvent, NoticeEvent

        match event:
            case NoticeEvent(title=title, severity=severity, description=description):
                self.notice(
                    session_id, agent, severity=severity, title=title, description=description
                )
            case CompactionEvent(phase=phase, trigger=trigger, pre_tokens=pre, error=error):
                status: CompactionStatus = "in_progress" if phase == "starting" else phase
                self.compaction(
                    session_id, agent, status=status, trigger=trigger, pre_tokens=pre, error=error
                )

    def since(self, position: int) -> ActivitySlice:
        """The kept entries after ``position``."""
        entries = [entry for entry in self._entries if entry.seq > position]
        first_kept = self._entries[0].seq if self._entries else self._position + 1
        truncated = position + 1 < first_kept and position < self._position
        return ActivitySlice(entries=entries, position=self._position, truncated=truncated)
