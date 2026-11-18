"""**UNSTABLE**: Session cursors: named positions in a session's timeline.

An Agent announces cursors with ``cursor_update`` session updates (only to Clients
that advertise ``session.cursors``). A cursor is anchored next to an entity the
Client holds (a message, tool call or compaction), and its capabilities say which
methods accept it, e.g. ``session/fork`` with ``cursorId``.

See the [Session Cursors RFD](https://agentclientprotocol.com/rfds/session-cursors).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Discriminator, Field, Tag

from acp.schema.base import AnnotatedObject


CursorId = str
"""**UNSTABLE**: Agent-owned ID of a cursor, unique within its session.

Never reused for another position. Clients MUST NOT parse, change or construct it.
"""

CursorAnchorPosition = Literal["before", "after"] | str
"""**UNSTABLE**: On which side of the anchored entity the cursor is.

``before``: the history before the cursor excludes the entity. ``after``: it includes
the entity and nothing that follows it. Values starting with ``_`` are extensions;
a Client that does not recognize the value cannot place the cursor.
"""


class CursorMessageAnchor(AnnotatedObject):
    """**UNSTABLE**: A cursor anchored to a message; ``type`` names the kind of message."""

    message_id: str
    """The ID of the anchored message."""

    position: CursorAnchorPosition
    """On which side of the message the cursor is."""


class UserMessageCursorAnchor(CursorMessageAnchor):
    """**UNSTABLE**: The cursor is next to a user message."""

    type: Literal["user_message"] = Field(default="user_message", init=False)


class AgentMessageCursorAnchor(CursorMessageAnchor):
    """**UNSTABLE**: The cursor is next to an agent message."""

    type: Literal["agent_message"] = Field(default="agent_message", init=False)


class AgentThoughtCursorAnchor(CursorMessageAnchor):
    """**UNSTABLE**: The cursor is next to an agent thought."""

    type: Literal["agent_thought"] = Field(default="agent_thought", init=False)


class SessionMessageCursorAnchor(CursorMessageAnchor):
    """**UNSTABLE**: The cursor is next to a session-directed message."""

    type: Literal["session_message"] = Field(default="session_message", init=False)


class ToolCallCursorAnchor(AnnotatedObject):
    """**UNSTABLE**: The cursor is next to a tool call."""

    type: Literal["tool_call"] = Field(default="tool_call", init=False)

    tool_call_id: str
    """The ID of the anchored tool call."""

    position: CursorAnchorPosition
    """On which side of the tool call the cursor is."""


class CompactionCursorAnchor(AnnotatedObject):
    """**UNSTABLE**: The cursor is next to a context compaction."""

    type: Literal["compaction"] = Field(default="compaction", init=False)

    compaction_id: str
    """The ID of the anchored compaction."""

    position: CursorAnchorPosition
    """On which side of the compaction the cursor is."""


class UnknownCursorAnchor(AnnotatedObject):
    """**UNSTABLE**: A cursor anchored to a custom or future kind of entity.

    The raw kind is kept in ``type``; values starting with ``_`` are extensions.
    A Client that does not recognize it cannot place the cursor.
    """

    type: str
    """The anchor kind."""


_KNOWN_ANCHOR_TYPES = frozenset({
    "user_message",
    "agent_message",
    "agent_thought",
    "session_message",
    "tool_call",
    "compaction",
})


def _anchor_discriminator(v: Any) -> str:
    kind = v.get("type") if isinstance(v, dict) else getattr(v, "type", None)
    return kind if kind in _KNOWN_ANCHOR_TYPES else "unknown"


CursorAnchor = Annotated[
    Annotated[UserMessageCursorAnchor, Tag("user_message")]
    | Annotated[AgentMessageCursorAnchor, Tag("agent_message")]
    | Annotated[AgentThoughtCursorAnchor, Tag("agent_thought")]
    | Annotated[SessionMessageCursorAnchor, Tag("session_message")]
    | Annotated[ToolCallCursorAnchor, Tag("tool_call")]
    | Annotated[CompactionCursorAnchor, Tag("compaction")]
    | Annotated[UnknownCursorAnchor, Tag("unknown")],
    Discriminator(_anchor_discriminator),
]
"""**UNSTABLE**: Where a cursor is, relative to an entity the Client holds.

A known ``type`` with a malformed payload is an error; it does not fall back to
:class:`UnknownCursorAnchor`.
"""


class CursorForkCapabilities(AnnotatedObject):
    """**UNSTABLE**: The cursor can be passed to ``session/fork`` as ``cursorId``."""


class CursorCapabilities(AnnotatedObject):
    """**UNSTABLE**: The methods that currently accept a cursor."""

    fork: CursorForkCapabilities | None = None
    """``session/fork`` accepts this cursor. ``None`` means unsupported."""
