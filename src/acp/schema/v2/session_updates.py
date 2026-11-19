"""v2 ``session/update``: the :data:`SessionUpdate` union and the smaller updates."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Annotated, Literal

from pydantic import MISSING, Discriminator, Field, Tag

from acp.schema.v2.base import OTHER_TAG, AnnotatedObject, Unknown, open_discriminator
from acp.schema.v2.commands import AvailableCommand  # noqa: TC001
from acp.schema.v2.common import CompactionId, Cost, SessionId  # noqa: TC001
from acp.schema.v2.config_options import SessionConfigOption  # noqa: TC001
from acp.schema.v2.content import ContentBlock  # noqa: TC001
from acp.schema.v2.cursors import CursorUpdate
from acp.schema.v2.messages import (
    AgentMessage,
    AgentMessageChunk,
    AgentThought,
    AgentThoughtChunk,
    SessionMessage,
    SessionMessageChunk,
    UserMessage,
    UserMessageChunk,
)
from acp.schema.v2.plans import PlanRemoved, PlanUpdate
from acp.schema.v2.prompt import (
    IdleStateUpdate,
    OtherStateUpdate,
    RequiresActionStateUpdate,
    RunningStateUpdate,
    UnknownStateUpdate,
)
from acp.schema.v2.subagents import SubagentUpdate
from acp.schema.v2.terminals import TerminalOutputChunk, TerminalUpdate
from acp.schema.v2.tool_calls import ToolCallContentChunk, ToolCallUpdate


class AvailableCommandsUpdate(AnnotatedObject):
    """The available commands are ready or have changed."""

    session_update: Literal["available_commands_update"] = Field(
        default="available_commands_update", init=False
    )

    available_commands: Sequence[AvailableCommand]
    """All commands the agent can execute."""


class ConfigOptionUpdate(AnnotatedObject):
    """The session's config options have changed."""

    session_update: Literal["config_option_update"] = Field(
        default="config_option_update", init=False
    )

    config_options: Sequence[SessionConfigOption]
    """All config options and their current values."""


class SessionInfoUpdate(AnnotatedObject):
    """Partial update of session metadata; ``None`` clears a field."""

    session_update: Literal["session_info_update"] = Field(
        default="session_info_update", init=False
    )

    title: str | MISSING | None = MISSING
    """Human-readable title of the session."""

    updated_at: str | MISSING | None = MISSING
    """RFC 3339 timestamp of the last activity."""


class UsageUpdate(AnnotatedObject):
    """Context window and cost of the session."""

    session_update: Literal["usage_update"] = Field(default="usage_update", init=False)

    used: int = Field(ge=0)
    """Tokens currently in the context."""

    size: int = Field(ge=0)
    """Total context window size in tokens."""

    cost: Cost | MISSING | None = MISSING
    """Cumulative session cost; omitted leaves the last cost unchanged, ``None`` clears it."""


NoticeSeverity = Literal["info", "warning", "error"] | str
"""Severity hint of a notice. Values starting with ``_`` are extensions."""


class Notice(AnnotatedObject):
    """Fire-and-forget information for the user, not part of session history.

    Agents must not rely on a notice being received or shown.
    """

    session_update: Literal["notice"] = Field(default="notice", init=False)

    severity: NoticeSeverity
    """Presentation severity hint."""

    title: str = Field(min_length=1)
    """Plain-text title that can stand alone."""

    description: str | MISSING | None = MISSING
    """Plain-text detail or guidance."""


CompactionStatus = Literal["in_progress", "completed", "failed", "cancelled"] | str
"""Lifecycle state of a context compaction. Values starting with ``_`` are extensions."""


class CompactionUpdate(AnnotatedObject):
    """A context compaction upsert, keyed by ``compaction_id``.

    The first update fixes the compaction's position in the timeline; ``summary`` and
    ``error`` are patches.
    """

    session_update: Literal["compaction_update"] = Field(default="compaction_update", init=False)

    compaction_id: CompactionId
    """The agent-owned ID of the compaction, unique within the session."""

    status: CompactionStatus
    """Current lifecycle status."""

    summary: Sequence[ContentBlock] | MISSING | None = MISSING
    """Complete replacement summary content to show the user."""

    error: str | MISSING | None = MISSING
    """Human-readable error details."""


class CompactionSummaryChunk(AnnotatedObject):
    """A content block appended to a compaction's summary.

    A first-seen ID creates an in-progress compaction; chunks append in receive order.
    """

    session_update: Literal["compaction_summary_chunk"] = Field(
        default="compaction_summary_chunk", init=False
    )

    compaction_id: CompactionId
    """The compaction whose summary receives the content."""

    content: ContentBlock
    """The appended content block."""


class ReplayComplete(AnnotatedObject):
    """The history replay requested by ``session/resume`` is complete.

    Sent once per ``session/resume`` with ``replayFrom``; a resume that ends without it
    delivered a truncated replay.
    """

    session_update: Literal["replay_complete"] = Field(default="replay_complete", init=False)


class PromptSuggestion(AnnotatedObject):
    """**UNSTABLE**: A prompt the agent predicts the user will send next."""

    prompt: Sequence[ContentBlock] = Field(min_length=1)
    """The content of the suggested prompt."""


class PromptSuggestionsUpdate(AnnotatedObject):
    """**UNSTABLE**: Replaces the session's prompt suggestions."""

    session_update: Literal["prompt_suggestions_update"] = Field(
        default="prompt_suggestions_update", init=False
    )

    suggestions: Sequence[PromptSuggestion]
    """Suggestions, most likely first; an empty list withdraws all."""


# --- state_update -----------------------------------------------------------------
# The ``state_update`` session update inlines a StateUpdate snapshot next to its
# ``sessionUpdate`` tag, so each state has a session update counterpart.


class RunningStateSessionUpdate(RunningStateUpdate):
    """``state_update``: foreground work is in progress."""

    session_update: Literal["state_update"] = Field(default="state_update", init=False)


class IdleStateSessionUpdate(IdleStateUpdate):
    """``state_update``: no foreground work is active."""

    session_update: Literal["state_update"] = Field(default="state_update", init=False)


class RequiresActionStateSessionUpdate(RequiresActionStateUpdate):
    """``state_update``: foreground work is blocked on user action."""

    session_update: Literal["state_update"] = Field(default="state_update", init=False)


class UnknownStateSessionUpdate(UnknownStateUpdate):
    """``state_update``: the agent cannot currently determine foreground activity."""

    session_update: Literal["state_update"] = Field(default="state_update", init=False)


class OtherStateSessionUpdate(OtherStateUpdate):
    """``state_update`` with a custom or future state."""

    session_update: Literal["state_update"] = Field(default="state_update", init=False)


StateSessionUpdate = Annotated[
    Annotated[RunningStateSessionUpdate, Tag("running")]
    | Annotated[IdleStateSessionUpdate, Tag("idle")]
    | Annotated[RequiresActionStateSessionUpdate, Tag("requires_action")]
    | Annotated[UnknownStateSessionUpdate, Tag("unknown")]
    | Annotated[OtherStateSessionUpdate, Tag(OTHER_TAG)],
    Discriminator(
        open_discriminator("state", frozenset({"running", "idle", "requires_action", "unknown"}))
    ),
]
"""A ``state_update`` session update: a snapshot of the session's foreground activity."""


class ToolCallSessionUpdate(ToolCallUpdate):
    """``tool_call_update``: a tool call was created or updated."""

    session_update: Literal["tool_call_update"] = Field(default="tool_call_update", init=False)


# --- The union --------------------------------------------------------------------


class UnknownSessionUpdate(Unknown):
    """A custom or future session update; values starting with ``_`` are extensions."""

    session_update: str
    """The update kind."""


_SESSION_UPDATES = frozenset({
    "user_message_chunk",
    "user_message",
    "agent_message_chunk",
    "agent_message",
    "agent_thought_chunk",
    "agent_thought",
    "state_update",
    "tool_call_content_chunk",
    "tool_call_update",
    "terminal_update",
    "terminal_output_chunk",
    "plan_update",
    "plan_removed",
    "available_commands_update",
    "config_option_update",
    "session_info_update",
    "usage_update",
    "notice",
    "compaction_update",
    "compaction_summary_chunk",
    "replay_complete",
    "prompt_suggestions_update",
    "cursor_update",
    "subagent_update",
    "session_message",
    "session_message_chunk",
})

SessionUpdate = Annotated[
    Annotated[UserMessageChunk, Tag("user_message_chunk")]
    | Annotated[UserMessage, Tag("user_message")]
    | Annotated[AgentMessageChunk, Tag("agent_message_chunk")]
    | Annotated[AgentMessage, Tag("agent_message")]
    | Annotated[AgentThoughtChunk, Tag("agent_thought_chunk")]
    | Annotated[AgentThought, Tag("agent_thought")]
    | Annotated[StateSessionUpdate, Tag("state_update")]
    | Annotated[ToolCallContentChunk, Tag("tool_call_content_chunk")]
    | Annotated[ToolCallSessionUpdate, Tag("tool_call_update")]
    | Annotated[TerminalUpdate, Tag("terminal_update")]
    | Annotated[TerminalOutputChunk, Tag("terminal_output_chunk")]
    | Annotated[PlanUpdate, Tag("plan_update")]
    | Annotated[PlanRemoved, Tag("plan_removed")]
    | Annotated[AvailableCommandsUpdate, Tag("available_commands_update")]
    | Annotated[ConfigOptionUpdate, Tag("config_option_update")]
    | Annotated[SessionInfoUpdate, Tag("session_info_update")]
    | Annotated[UsageUpdate, Tag("usage_update")]
    | Annotated[Notice, Tag("notice")]
    | Annotated[CompactionUpdate, Tag("compaction_update")]
    | Annotated[CompactionSummaryChunk, Tag("compaction_summary_chunk")]
    | Annotated[ReplayComplete, Tag("replay_complete")]
    | Annotated[PromptSuggestionsUpdate, Tag("prompt_suggestions_update")]
    | Annotated[CursorUpdate, Tag("cursor_update")]
    | Annotated[SubagentUpdate, Tag("subagent_update")]
    | Annotated[SessionMessage, Tag("session_message")]
    | Annotated[SessionMessageChunk, Tag("session_message_chunk")]
    | Annotated[UnknownSessionUpdate, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("sessionUpdate", _SESSION_UPDATES)),
]
"""An update sent while a session exists: messages, progress and other activity."""


class UpdateSessionNotification(AnnotatedObject):
    """Parameters of ``session/update``: one update of a session."""

    session_id: SessionId
    """The session the update belongs to."""

    update: SessionUpdate
    """The update."""
