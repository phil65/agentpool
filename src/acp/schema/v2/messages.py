"""v2 message updates: streamed chunks and whole-message upserts.

Every chunk carries the ``messageId`` of its message. Message upserts are keyed by
``messageId``; their ``content`` replaces the message content (omitted leaves it
unchanged, ``None`` clears it), while chunks append to it.
"""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Literal

from pydantic import MISSING, Field

from acp.schema.v2.base import AnnotatedObject
from acp.schema.v2.common import MessageId, SessionId  # noqa: TC001
from acp.schema.v2.content import ContentBlock  # noqa: TC001


class _Chunk(AnnotatedObject):
    message_id: MessageId
    """The message this chunk belongs to; shared by all its chunks."""

    content: ContentBlock
    """The content appended to the message."""


class UserMessageChunk(_Chunk):
    """A chunk of the user's message being streamed."""

    session_update: Literal["user_message_chunk"] = Field(default="user_message_chunk", init=False)


class AgentMessageChunk(_Chunk):
    """A chunk of the agent's response being streamed."""

    session_update: Literal["agent_message_chunk"] = Field(
        default="agent_message_chunk", init=False
    )


class AgentThoughtChunk(_Chunk):
    """A chunk of the agent's internal reasoning being streamed."""

    session_update: Literal["agent_thought_chunk"] = Field(
        default="agent_thought_chunk", init=False
    )


class _MessageUpsert(AnnotatedObject):
    message_id: MessageId
    """The message to create or update."""

    content: Sequence[ContentBlock] | MISSING | None = MISSING
    """Complete replacement content; omitted leaves it unchanged, ``None`` clears it."""


class UserMessage(_MessageUpsert):
    """A user message upsert, e.g. when the agent accepts or replaces a prompt."""

    session_update: Literal["user_message"] = Field(default="user_message", init=False)


class AgentMessage(_MessageUpsert):
    """An agent message upsert, in addition to or instead of streamed chunks."""

    session_update: Literal["agent_message"] = Field(default="agent_message", init=False)


class AgentThought(_MessageUpsert):
    """An agent thought (reasoning) message upsert."""

    session_update: Literal["agent_thought"] = Field(default="agent_thought", init=False)


class SessionMessage(AnnotatedObject):
    """An upsert of a message sent to or received from another session.

    Every field but ``message_id`` is a patch: omitted leaves it unchanged, ``None``
    clears it, a value replaces it.
    """

    session_update: Literal["session_message"] = Field(default="session_message", init=False)

    message_id: MessageId
    """Identifier of the message in this session's transcript."""

    sender_session_id: SessionId | MISSING | None = MISSING
    """The sending session."""

    recipient_session_id: SessionId | MISSING | None = MISSING
    """The receiving session."""

    sender_message_id: MessageId | MISSING | None = MISSING
    """On an incoming view: the ``messageId`` of the outgoing view in the sender's transcript."""

    recipient_message_id: MessageId | MISSING | None = MISSING
    """On an outgoing view: the ``messageId`` of the incoming view in the recipient's
    transcript."""

    content: Sequence[ContentBlock] | MISSING | None = MISSING
    """Complete replacement content."""


class SessionMessageChunk(AnnotatedObject):
    """A content block appended to a session message.

    Participants and references come from ``session_message`` upserts.
    """

    session_update: Literal["session_message_chunk"] = Field(
        default="session_message_chunk", init=False
    )

    message_id: MessageId
    """Identifier of the message in this session's transcript."""

    content: ContentBlock
    """The appended content block."""
