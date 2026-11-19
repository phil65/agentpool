"""**UNSTABLE**: v2 session cursors: named positions in a session's timeline.

A cursor is anchored next to an entity the client holds (a message, tool call or
compaction); its capabilities say which methods accept it, e.g. ``session/fork``
with ``cursorId``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import MISSING, Discriminator, Field, Tag

from acp.schema.v2.base import OTHER_TAG, AnnotatedObject, Unknown, open_discriminator
from acp.schema.v2.common import CompactionId, CursorId, MessageId, ToolCallId  # noqa: TC001


CursorAnchorPosition = Literal["before", "after"] | str
"""**UNSTABLE**: On which side of the anchored entity the cursor is.

``before``: the history before the cursor excludes the entity; ``after``: it includes
it and nothing that follows. A client that does not know the value cannot place the
cursor.
"""


class CursorMessageAnchor(AnnotatedObject):
    """**UNSTABLE**: A cursor anchored to a message; ``type`` names the kind of message."""

    message_id: MessageId
    """The anchored message."""

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


class CursorToolCallAnchor(AnnotatedObject):
    """**UNSTABLE**: The cursor is next to a tool call."""

    type: Literal["tool_call"] = Field(default="tool_call", init=False)

    tool_call_id: ToolCallId
    """The anchored tool call."""

    position: CursorAnchorPosition
    """On which side of the tool call the cursor is."""


class CursorCompactionAnchor(AnnotatedObject):
    """**UNSTABLE**: The cursor is next to a context compaction."""

    type: Literal["compaction"] = Field(default="compaction", init=False)

    compaction_id: CompactionId
    """The anchored compaction."""

    position: CursorAnchorPosition
    """On which side of the compaction the cursor is."""


class UnknownCursorAnchor(Unknown):
    """**UNSTABLE**: A custom or future anchor kind; the client cannot place such a cursor."""

    type: str
    """The anchor kind."""


CursorAnchor = Annotated[
    Annotated[UserMessageCursorAnchor, Tag("user_message")]
    | Annotated[AgentMessageCursorAnchor, Tag("agent_message")]
    | Annotated[AgentThoughtCursorAnchor, Tag("agent_thought")]
    | Annotated[SessionMessageCursorAnchor, Tag("session_message")]
    | Annotated[CursorToolCallAnchor, Tag("tool_call")]
    | Annotated[CursorCompactionAnchor, Tag("compaction")]
    | Annotated[UnknownCursorAnchor, Tag(OTHER_TAG)],
    Discriminator(
        open_discriminator(
            "type",
            frozenset({
                "user_message",
                "agent_message",
                "agent_thought",
                "session_message",
                "tool_call",
                "compaction",
            }),
        )
    ),
]
"""**UNSTABLE**: Where a cursor is, relative to an entity the client holds."""


class CursorForkCapabilities(AnnotatedObject):
    """**UNSTABLE**: The cursor can be passed to ``session/fork`` as ``cursorId``."""


class CursorCapabilities(AnnotatedObject):
    """**UNSTABLE**: The methods that currently accept a cursor.

    A malformed value counts as no capabilities.
    """

    fork: CursorForkCapabilities | MISSING | None = MISSING
    """``session/fork`` accepts this cursor."""


class CursorUpdate(AnnotatedObject):
    """**UNSTABLE**: A session cursor upsert, keyed by ``cursor_id``.

    The first update for a cursor MUST include ``anchor``, which is fixed afterwards.
    ``capabilities`` and ``_meta`` are patches: omitted leaves them unchanged, ``None``
    clears them.
    """

    session_update: Literal["cursor_update"] = Field(default="cursor_update", init=False)

    cursor_id: CursorId
    """The agent-owned ID of the cursor, unique within the session."""

    capabilities: CursorCapabilities | MISSING | None = MISSING
    """The methods that currently accept the cursor; a new cursor starts with none."""

    anchor: CursorAnchor | MISSING | None = MISSING
    """Where the cursor is; required on the first update for a cursor."""
