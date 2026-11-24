"""Turns: a prompt run in a session of a node, with its outcome and final answer.

A turn is what agentpool runs: a prompt goes into a session, the node works (model
requests, tool calls, subagents), and the turn ends with an outcome and, usually, an
answer. Each agent turn builds a :class:`Turn` as it ends: the turn's final
``StreamCompleteEvent`` carries it, ``BaseAgent.turn_completed`` announces it (also for
failed and cancelled turns), and ``BaseAgent.run_turn`` returns it. A turn's ID is its
prompt's message ID, under which storage keeps its answer.

See ``ARCHITECTURE_TURNS.md`` (stage 1).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from datetime import datetime

    from pydantic_ai import RunUsage

    from agentpool.messaging import ChatMessage
    from agentpool.messaging.messages import TokenCost
    from agentpool.sessions.activity import StopReason


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    """The node finished the turn (or the prompt held only commands)."""


@dataclass(frozen=True, slots=True)
class TurnCancelled:
    """The turn was cancelled; ``Turn.result`` holds the partial answer, if there is one."""


@dataclass(frozen=True, slots=True)
class TurnLimitReached:
    """A usage limit (requests, tokens, tool calls) ended the turn."""

    message: str


@dataclass(frozen=True, slots=True)
class TurnFailed:
    """The node failed during the turn."""

    error: Exception


@dataclass(frozen=True, slots=True)
class TurnRefused:
    """The turn did not run, e.g. because the prompt was empty."""

    reason: str


type TurnOutcome = TurnCompleted | TurnCancelled | TurnLimitReached | TurnFailed | TurnRefused
"""How a turn ended."""


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnRef:
    """Where a turn is: enough to find it in storage."""

    session_id: str
    turn_id: str
    node: str


@dataclass(kw_only=True)
class Turn[TResult = Any]:
    """A prompt run in a session of a node: how it ended, and its answer."""

    turn_id: str
    """The prompt's message ID (storage keeps the answer under it)."""
    session_id: str
    node: str
    """The agent (or team) that ran it."""
    prompt: ChatMessage[Any]
    """The prompt (``role="user"``)."""
    outcome: TurnOutcome
    result: ChatMessage[TResult] | None = None
    """The final answer: None if the turn failed or was cancelled before answering."""
    usage: RunUsage
    """The turn's own model usage (not its children's)."""
    cost: TokenCost | None = None
    started_at: datetime
    duration: float
    """Seconds from the prompt to the end."""
    parent: TurnRef | None = None
    """The turn that caused this one (a subagent call, a routing source), if known."""
    children: list[Turn[Any]] = field(default_factory=list)
    """Turns this one spawned (subagent runs), as far as they are tracked."""

    @property
    def ref(self) -> TurnRef:
        """The reference of the turn."""
        return TurnRef(session_id=self.session_id, turn_id=self.turn_id, node=self.node)

    @property
    def completed(self) -> bool:
        """Whether the turn ended normally."""
        return isinstance(self.outcome, TurnCompleted)


def outcome_of_error(error: BaseException) -> TurnOutcome:
    """The outcome of a turn that ended with ``error``."""
    from pydantic_ai import UsageLimitExceeded

    match error:
        case asyncio.CancelledError():
            return TurnCancelled()
        case UsageLimitExceeded():
            return TurnLimitReached(message=str(error))
        case Exception():
            return TurnFailed(error=error)
        case _:  # KeyboardInterrupt and the like end the turn as a failure too
            return TurnFailed(error=RuntimeError(f"{type(error).__name__}: {error}"))


def stop_reason(outcome: TurnOutcome) -> StopReason:
    """The activity feed's stop reason for a turn's outcome."""
    match outcome:
        case TurnCompleted() | TurnRefused():
            return "end_turn"
        case TurnCancelled():
            return "cancelled"
        case TurnLimitReached() | TurnFailed():
            return "error"
