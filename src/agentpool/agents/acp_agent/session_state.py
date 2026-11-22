"""ACP Agent - Session state tracking."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field as dataclass_field
from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger


if TYPE_CHECKING:
    from aclientp.schema import (
        AvailableCommandsUpdate,
        SessionConfigOption,
        SessionModeState,
        SessionUpdate,
    )

    from agentpool.ui.base import InputProvider

logger = get_logger(__name__)

PROTOCOL_VERSION = 1


class TimeoutableEvent(asyncio.Event):
    """Event that can be waited on with a timeout."""

    async def wait_with_timeout(self, timeout: float | None = None) -> bool:
        if timeout:
            return await asyncio.wait_for(super().wait(), timeout)
        return await super().wait()


@dataclass
class ACPSessionState:
    """Tracks state of an ACP session.

    Raw ACP SessionUpdate objects are stored as the single source of truth.
    Conversion to native events happens lazily during streaming consumption.
    """

    session_id: str
    """The session ID from the ACP server."""

    pool_session_id: str | None = None
    """The pool session this ACP session belongs to; None for the agent's default one."""

    update_event: TimeoutableEvent = dataclass_field(default_factory=TimeoutableEvent)
    """Set when an update of this session arrives (the turn's stream waits on it)."""

    input_provider: InputProvider | None = None
    """Input provider of the turn running on this session (permission requests)."""

    prompt_task: asyncio.Task[Any] | None = None
    """The ``session/prompt`` request of the turn running on this session."""

    updates: deque[SessionUpdate] = dataclass_field(default_factory=deque)
    """Raw ACP session updates - single source of truth for stream data."""

    modes: SessionModeState | None = None
    """Full mode state including available modes (legacy)."""

    current_mode_id: str | None = None
    """Current mode ID (legacy)."""

    config_options: list[SessionConfigOption] = dataclass_field(default_factory=list)
    """Unified session config options (replaces modes/models in newer ACP versions)."""

    available_commands: AvailableCommandsUpdate | None = None
    """Available commands from the agent."""

    is_loading: bool = False
    """Flag indicating session is being loaded (collecting updates for replay)."""

    _load_updates: list[SessionUpdate] = dataclass_field(default_factory=list)
    """Separate list for collecting updates during load (not consumed by streaming)."""

    def clear(self) -> None:
        """Clear stream-related state for a new prompt turn."""
        self.updates.clear()
        # Note: Don't clear modes, config_options - those persist

    def add_update(self, update: SessionUpdate) -> None:
        """Add a raw ACP update to the queue."""
        self.updates.append(update)
        # Also collect for load if we're loading
        if self.is_loading:
            self._load_updates.append(update)

    def pop_update(self) -> SessionUpdate | None:
        """Pop and return the next update, or None if empty."""
        if self.updates:
            return self.updates.popleft()
        return None

    def has_pending_updates(self) -> bool:
        """Check if there are unconsumed updates."""
        return len(self.updates) > 0

    def start_load(self) -> None:
        """Start collecting updates for session load."""
        self.is_loading = True
        self._load_updates.clear()

    def finish_load(self) -> list[SessionUpdate]:
        """Finish loading and return collected updates."""
        self.is_loading = False
        updates = list(self._load_updates)
        self._load_updates.clear()
        return updates
