"""v2 prompt lifecycle: ``session/prompt`` and the session's foreground state.

``session/prompt`` returns as soon as the user message is inserted, with its
``messageId``; it does not wait for the turn. Progress is reported with
``state_update`` notifications (``running``, ``idle``, ``requires_action``, ``unknown``),
and the stop reason travels on the ``idle`` update. A failure after insertion ends
foreground work with ``idle`` and ``stopReason: "error"``; a JSON-RPC error on
``session/prompt`` always means the message was not inserted.
"""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Annotated, Literal

from pydantic import MISSING, Discriminator, Field, Tag

from acp.schema.v2.base import OTHER_TAG, AnnotatedObject, Unknown, open_discriminator
from acp.schema.v2.common import (  # noqa: TC001
    Error,
    IdempotencyKey,
    MessageId,
    SessionId,
    TerminalId,
    ToolCallId,
    Usage,
)
from acp.schema.v2.content import ContentBlock  # noqa: TC001


class PromptRequest(AnnotatedObject):
    """Parameters of ``session/prompt``: a user message for the session.

    Agents MUST support ``text`` and ``resource_link`` content; other block types
    need the matching prompt capability.
    """

    session_id: SessionId
    """The session to send the message to."""

    prompt: Sequence[ContentBlock]
    """The content of the user message."""

    idempotency_key: IdempotencyKey | MISSING | None = MISSING
    """Identifies this submission, so a retry does not insert the message twice."""


class PromptResponse(AnnotatedObject):
    """Acknowledges that the user message was inserted into the conversation.

    It does not mean the turn has finished; that is reported with ``state_update``.
    """

    message_id: MessageId
    """ID of the inserted user message; live user message updates use the same ID."""


# --- Background work --------------------------------------------------------------


class BackgroundTerminal(AnnotatedObject):
    """A process that keeps running after its tool call returned control to the model."""

    type: Literal["terminal"] = Field(default="terminal", init=False)

    terminal_id: TerminalId
    """The listed terminal."""


class BackgroundToolCall(AnnotatedObject):
    """Work of a tool call that continues without a process of its own, e.g. a watcher."""

    type: Literal["tool_call"] = Field(default="tool_call", init=False)

    tool_call_id: ToolCallId
    """The listed tool call."""


class BackgroundSubagent(AnnotatedObject):
    """A detached subagent session that has anything running."""

    type: Literal["subagent"] = Field(default="subagent", init=False)

    session_id: SessionId
    """The listed session."""


class UnknownBackgroundWork(Unknown):
    """Custom or future background work; ``type`` values starting with ``_`` are extensions."""

    type: str
    """The kind of work."""


BackgroundWork = Annotated[
    Annotated[BackgroundTerminal, Tag("terminal")]
    | Annotated[BackgroundToolCall, Tag("tool_call")]
    | Annotated[BackgroundSubagent, Tag("subagent")]
    | Annotated[UnknownBackgroundWork, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("type", frozenset({"terminal", "tool_call", "subagent"}))),
]
"""One piece of work running independently of the foreground work.

Items reference entities the client already tracks; the agent announces each item
before listing it.
"""


# --- State --------------------------------------------------------------------------

StopReason = (
    Literal["end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled", "error"] | str
)
"""Why foreground work stopped. Values starting with ``_`` are extensions."""


class _StateBase(AnnotatedObject):
    background: Sequence[BackgroundWork] | MISSING | None = MISSING
    """Work running independently of foreground work, as a complete snapshot."""


class RunningStateUpdate(_StateBase):
    """Foreground work is in progress."""

    state: Literal["running"] = Field(default="running", init=False)


class IdleStateUpdate(_StateBase):
    """No foreground work is active; background work may continue.

    Agents SHOULD include ``stop_reason`` when the transition ends foreground work.
    """

    state: Literal["idle"] = Field(default="idle", init=False)

    stop_reason: StopReason | MISSING | None = MISSING
    """Why foreground work stopped; omitted or ``None`` when there is no stop reason."""

    error: Error | MISSING | None = MISSING
    """For ``stop_reason == "error"``: the failure, as a JSON-RPC error object."""

    usage: Usage | MISSING | None = MISSING
    """**UNSTABLE**: Token usage of the completed foreground work."""


class RequiresActionStateUpdate(_StateBase):
    """The foreground workflow as a whole is blocked on user action."""

    state: Literal["requires_action"] = Field(default="requires_action", init=False)


class UnknownStateUpdate(_StateBase):
    """The agent cannot currently determine foreground activity.

    Replaces previously confirmed states; not to be sent merely because the session
    has been quiet.
    """

    state: Literal["unknown"] = Field(default="unknown", init=False)


class OtherStateUpdate(Unknown, _StateBase):
    """A custom or future state; ``state`` values starting with ``_`` are extensions."""

    state: str
    """The state."""


StateUpdate = Annotated[
    Annotated[RunningStateUpdate, Tag("running")]
    | Annotated[IdleStateUpdate, Tag("idle")]
    | Annotated[RequiresActionStateUpdate, Tag("requires_action")]
    | Annotated[UnknownStateUpdate, Tag("unknown")]
    | Annotated[OtherStateUpdate, Tag(OTHER_TAG)],
    Discriminator(
        open_discriminator("state", frozenset({"running", "idle", "requires_action", "unknown"}))
    ),
]
"""A snapshot of the session's aggregate foreground activity."""
