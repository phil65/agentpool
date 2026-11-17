"""Session state schema definitions."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Literal

from pydantic import Field

from acp.schema.base import AnnotatedObject


class SessionMode(AnnotatedObject):
    """A mode the agent can operate in.

    See protocol docs: [Session Modes](https://agentclientprotocol.com/protocol/session-modes)
    """

    description: str | None = None
    """Optional description of the mode."""

    id: str
    """Unique identifier for the mode."""

    name: str
    """Human-readable name of the mode."""


class SessionModeState(AnnotatedObject):
    """The set of modes and the one currently active."""

    available_modes: Sequence[SessionMode]
    """The set of modes that the Agent can operate in."""

    current_mode_id: str
    """The current mode the Agent is in."""


class SessionInfo(AnnotatedObject):
    """Information about a session returned by session/list."""

    cwd: str
    """The working directory for this session. Must be an absolute path."""

    additional_directories: Sequence[str] = Field(default_factory=list)
    """Additional workspace roots reported for this session. Each path must be absolute."""

    session_id: str
    """Unique identifier for the session."""

    title: str | None = None
    """Human-readable title for the session."""

    updated_at: str | None = None
    """ISO 8601 timestamp of last activity."""

    archived: bool | None = None
    """**UNSTABLE**: Whether the session is archived."""


SessionStatus = Literal["open", "saved"] | str
"""**UNSTABLE**: Whether the Agent has the session in memory.

- ``open``: The Agent has the session in memory, for any connection.
- ``saved``: The Agent knows the session and can resume it, but does not have it in memory.
"""

SessionArchiveFilter = Literal["exclude", "include", "only"] | str
"""**UNSTABLE**: Which sessions ``session/list`` returns by archive state.

``exclude`` (the default) returns only sessions that are not archived.
"""
