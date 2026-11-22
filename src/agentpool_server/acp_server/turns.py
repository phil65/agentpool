"""Protocol-neutral prompt turns of an ACP session.

A turn's outcome and where its events go are independent of the ACP version: v1
answers ``session/prompt`` with a stop reason once the turn ended, v2 reports the
turn through ``state_update`` notifications (see ``ACP_V2_SERVER.md``). The session
runs the turn (``ACPSession.run_turn``); each protocol supplies a ``TurnOutput`` and
maps the ``TurnOutcome``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol


if TYPE_CHECKING:
    from agentpool.agents.events import RichAgentStreamEvent


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    """The agent finished the turn (or the prompt held only slash commands)."""


@dataclass(frozen=True, slots=True)
class TurnCancelled:
    """The client cancelled the turn; it may have left tool calls pending."""


@dataclass(frozen=True, slots=True)
class TurnLimitReached:
    """A usage limit (requests, tokens, tool calls) ended the turn."""

    message: str


@dataclass(frozen=True, slots=True)
class TurnFailed:
    """The agent failed during the turn."""

    error: Exception


@dataclass(frozen=True, slots=True)
class TurnRefused:
    """The turn did not run, e.g. because the prompt was empty."""

    reason: str


type TurnOutcome = TurnCompleted | TurnCancelled | TurnLimitReached | TurnFailed | TurnRefused


class TurnOutput(Protocol):
    """Where a turn's agent events go, in the shape of one ACP version."""

    async def start(self) -> None:
        """Prepare for the agent's events; called once the prompt's commands ran."""
        ...

    async def event(self, event: RichAgentStreamEvent[Any]) -> None:
        """Report one agent event to the client."""
        ...

    async def finish(self, outcome: TurnOutcome) -> None:
        """Wrap up the turn's output (pending tool calls, error notices).

        Called while the turn still holds the session, so nothing of the next turn
        interleaves with it.
        """
        ...
