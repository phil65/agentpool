"""v2 session lifecycle: create, resume, fork, close, list and manage sessions.

``session/load`` is gone in v2: ``session/resume`` covers both resuming without
replay and full replay, selected with ``replayFrom``.
"""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Annotated, Literal

from pydantic import MISSING, Discriminator, Field, Tag

from acp.schema.v2.base import OTHER_TAG, AnnotatedObject, Unknown, open_discriminator
from acp.schema.v2.commands import AvailableCommand  # noqa: TC001
from acp.schema.v2.common import (  # noqa: TC001
    AbsolutePath,
    CursorId,
    SessionId,
    SessionListCursor,
)
from acp.schema.v2.config_options import (  # noqa: TC001
    InitialSessionConfigOption,
    SessionConfigOption,
)
from acp.schema.v2.content import ContentBlock  # noqa: TC001
from acp.schema.v2.mcp import McpServer  # noqa: TC001
from acp.schema.v2.prompt import StateUpdate  # noqa: TC001


SystemPromptMode = Literal["append", "override"] | str
"""How the agent combines a client-provided system prompt with its own instructions.

Every agent with the ``systemPrompt`` capability supports ``append``; ``override``
needs ``session.systemPrompt.mode.override``. Values starting with ``_`` are extensions.
"""

SessionArchiveFilter = Literal["exclude", "include", "only"] | str
"""**UNSTABLE**: Which sessions ``session/list`` returns by archive state (default ``exclude``)."""

SessionStatus = Literal["open", "saved"] | str
"""**UNSTABLE**: Whether the agent has a session in memory (``open``) or can resume it (``saved``).

Clients MUST treat unknown values as ``saved``.
"""


class SystemPrompt(AnnotatedObject):
    """Client-provided system-level instructions for a session.

    Only sent when the agent advertises ``session.systemPrompt``; agents that cannot apply
    it return an error instead of creating the session.
    """

    mode: SystemPromptMode
    """How the Agent combines ``content`` with its own instructions."""

    content: Sequence[ContentBlock]
    """The system prompt content."""


class NewSessionRequest(AnnotatedObject):
    """Request parameters for creating a new session."""

    cwd: AbsolutePath
    """The working directory for this session. Must be an absolute path."""

    additional_directories: Sequence[AbsolutePath] | MISSING = MISSING
    """Additional workspace roots for this session. Each path must be absolute."""

    mcp_servers: Sequence[McpServer] | MISSING = MISSING
    """List of MCP (Model Context Protocol) servers the agent should connect to."""

    system_prompt: SystemPrompt | MISSING | None = MISSING
    """System-level instructions for the new session."""

    config_options: Sequence[InitialSessionConfigOption] | MISSING = MISSING
    """Initial values for the session's config options."""


class NewSessionResponse(AnnotatedObject):
    """Response from creating a new session.

    New sessions are ``idle`` until a ``state_update`` says otherwise.
    """

    session_id: SessionId
    """Unique identifier for the created session."""

    config_options: Sequence[SessionConfigOption] | MISSING = MISSING
    """Initial session configuration options."""

    available_commands: Sequence[AvailableCommand] | MISSING = MISSING
    """Initial commands the agent can execute in this session."""


class ReplayFromStart(AnnotatedObject):
    """Replay all retained conversation history from its first replayable entry."""

    type: Literal["start"] = Field(default="start", init=False)


class UnknownReplayFrom(Unknown):
    """A custom or future replay cursor; ``type`` values starting with ``_`` are extensions."""

    type: str
    """The cursor kind."""


ReplayFrom = Annotated[
    Annotated[ReplayFromStart, Tag("start")] | Annotated[UnknownReplayFrom, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("type", frozenset({"start"}))),
]
"""Inclusive cursor of where replayed history begins on ``session/resume``."""


class ResumeSessionRequest(AnnotatedObject):
    """Request parameters for resuming an existing session."""

    session_id: SessionId
    """The ID of the session to resume."""

    cwd: AbsolutePath
    """The working directory for this session. Must be an absolute path."""

    additional_directories: Sequence[AbsolutePath] | MISSING = MISSING
    """Additional workspace roots to activate for this session. Each path must be absolute."""

    mcp_servers: Sequence[McpServer] | MISSING = MISSING
    """List of MCP servers to connect to for this session."""

    replay_from: ReplayFrom | MISSING | None = MISSING
    """Inclusive cursor describing where conversation replay should begin."""

    system_prompt: SystemPrompt | MISSING | None = MISSING
    """System-level instructions for the resumed session."""

    config_options: Sequence[InitialSessionConfigOption] | MISSING = MISSING
    """Initial values for the session's config options."""


class ResumeSessionResponse(AnnotatedObject):
    """Response from resuming an existing session.

    The agent sends a current ``state_update`` after any requested replay (ended by
    ``replay_complete``) and before this response.
    """

    config_options: Sequence[SessionConfigOption] | MISSING = MISSING
    """Initial session configuration options."""

    available_commands: Sequence[AvailableCommand] | MISSING = MISSING
    """Initial commands the agent can execute in this session."""


class ForkSessionRequest(AnnotatedObject):
    """**UNSTABLE**: Request parameters for forking an existing session."""

    session_id: SessionId
    """The ID of the session to fork."""

    cursor_id: CursorId | MISSING | None = MISSING
    """The session cursor to fork at. The new session's history is the source session's history
    before the cursor."""

    cwd: AbsolutePath
    """The working directory for this session. Must be an absolute path."""

    additional_directories: Sequence[AbsolutePath] | MISSING = MISSING
    """Additional workspace roots to activate for this session. Each path must be absolute."""

    mcp_servers: Sequence[McpServer] | MISSING = MISSING
    """List of MCP servers to connect to for this session."""

    system_prompt: SystemPrompt | MISSING | None = MISSING
    """System-level instructions for the forked session."""

    config_options: Sequence[InitialSessionConfigOption] | MISSING = MISSING
    """Initial values for the forked session's config options."""


class ForkSessionResponse(AnnotatedObject):
    """**UNSTABLE**: Response from forking an existing session."""

    session_id: SessionId
    """Unique identifier for the newly created forked session."""

    config_options: Sequence[SessionConfigOption] | MISSING = MISSING
    """Initial session configuration options."""

    available_commands: Sequence[AvailableCommand] | MISSING = MISSING
    """Initial commands the agent can execute in this session."""


class CloseSessionRequest(AnnotatedObject):
    """Request parameters for closing an active session."""

    session_id: SessionId
    """The ID of the session to close."""


class CloseSessionResponse(AnnotatedObject):
    """Response from closing a session."""


class CancelSessionNotification(AnnotatedObject):
    """Notification to cancel a session's foreground work and its prompts not yet inserted."""

    session_id: SessionId
    """The ID of the session whose foreground work to cancel."""


class ListSessionsRequest(AnnotatedObject):
    """Request parameters for listing existing sessions."""

    cwd: AbsolutePath | MISSING | None = MISSING
    """Filter sessions by working directory. Must be an absolute path."""

    cursor: SessionListCursor | MISSING | None = MISSING
    """Opaque cursor token from a previous response's nextCursor field for cursor-based
    pagination"""

    archived: SessionArchiveFilter | MISSING | None = MISSING
    """Filter sessions by archive state."""


class SessionInfo(AnnotatedObject):
    """Information about a session returned by ``session/list``."""

    session_id: SessionId
    """Unique identifier for the session"""

    cwd: AbsolutePath
    """The working directory for this session. Must be an absolute path."""

    additional_directories: Sequence[AbsolutePath] | MISSING = MISSING
    """Additional workspace roots reported for this session. Each path must be absolute."""

    title: str | MISSING | None = MISSING
    """Human-readable title for the session"""

    updated_at: str | MISSING | None = MISSING
    """RFC 3339 timestamp of last activity."""

    archived: bool | MISSING | None = MISSING
    """Whether the session is archived."""


class ListSessionsResponse(AnnotatedObject):
    """Response from listing sessions."""

    sessions: Sequence[SessionInfo]
    """Array of session information objects."""

    next_cursor: SessionListCursor | MISSING | None = MISSING
    """Opaque cursor token. If present, pass this in the next request's cursor parameter to
    fetch the next page. An empty string is a valid cursor, not the end of results."""


class DeleteSessionRequest(AnnotatedObject):
    """Request parameters for deleting an existing session from ``session/list``."""

    session_id: SessionId
    """The ID of the session to delete."""


class DeleteSessionResponse(AnnotatedObject):
    """Response from deleting a session."""


class ArchiveSessionRequest(AnnotatedObject):
    """**UNSTABLE**: Request parameters for archiving a session."""

    session_id: SessionId
    """The ID of the session to archive."""


class ArchiveSessionResponse(AnnotatedObject):
    """**UNSTABLE**: Response from archiving a session."""


class UnarchiveSessionRequest(AnnotatedObject):
    """**UNSTABLE**: Request parameters for unarchiving a session."""

    session_id: SessionId
    """The ID of the session to unarchive."""


class UnarchiveSessionResponse(AnnotatedObject):
    """**UNSTABLE**: Response from unarchiving a session."""


class SessionStatusRequest(AnnotatedObject):
    """**UNSTABLE**: Request parameters for checking the status of a session."""

    session_id: SessionId
    """The ID of the session to check."""


class SessionStatusResponse(AnnotatedObject):
    """**UNSTABLE**: Response from checking the status of a session."""

    status: SessionStatus
    """Whether the Agent has the session in memory."""

    state: StateUpdate | MISSING | None = MISSING
    """The session's current foreground state, as the same snapshot the Agent sends in
    ``state_update`` notifications."""


class SetSessionTitleRequest(AnnotatedObject):
    """**UNSTABLE**: Request parameters for setting the title of a session."""

    session_id: SessionId
    """The ID of the session to retitle."""

    title: str
    """The new title for the session."""


class SetSessionTitleResponse(AnnotatedObject):
    """**UNSTABLE**: Response from setting the title of a session."""

    title: str
    """The title the Agent stored."""


class SessionOptionsRequest(AnnotatedObject):
    """**UNSTABLE**: Parameters of ``session/options``.

    Returns the config options a session would start with, without creating or resuming
    one.
    """

    cwd: AbsolutePath
    """The working directory to resolve options for. Must be an absolute path."""

    additional_directories: Sequence[AbsolutePath] | MISSING = MISSING
    """Additional workspace roots to resolve options for. Each path must be absolute."""

    session_id: SessionId | MISSING | None = MISSING
    """The session to resolve options for."""


class SessionOptionsResponse(AnnotatedObject):
    """**UNSTABLE**: Response from ``session/options``."""

    config_options: Sequence[SessionConfigOption] | MISSING = MISSING
    """The config options the session would start with, with the same rules as on
    ``NewSessionResponse``."""

    available_commands: Sequence[AvailableCommand] | MISSING = MISSING
    """Commands the Agent knows the session would have."""
