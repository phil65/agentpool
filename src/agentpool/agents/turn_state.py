"""Live state of one session's turns on an agent instance.

An agent instance can serve several sessions (``run_stream(session=...)``). What a turn
changes while it runs belongs to its session, not to the instance: whether it was
cancelled, its task, prompts queued or injected into it, events of its tools and
subagents, and content staged for its next prompt. ``BaseAgent`` keeps one
``TurnState`` per session and resolves the one of the running turn through a context
variable, so tools (``ctx.agent.queue_prompt(...)``) and agent implementations
(``self._cancelled``) reach their own turn's state.

The variable maps agents to their active turn rather than holding one turn: a turn of
agent A can stream a subagent B's turn (``task``), and both must find their own.
This is the first part of a runtime per session (``ARCHITECTURE_SESSIONS_AGENTS.md``
§6 step 7, §11): one instance can run turns of different sessions concurrently.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from agentpool.agents.prompt_injection import PromptInjectionManager
from agentpool.agents.staged_content import StagedContent


if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.sessions import Session


@dataclass(eq=False)
class TurnState:
    """What a session's turns on one agent instance change while they run."""

    session: Session
    """The session these turns belong to."""
    cancelled: bool = False
    task: asyncio.Task[Any] | None = None
    """Task of the running ``run_stream`` (agents cancel it on interrupt)."""
    active_runs: int = 0
    """Runs currently streaming (events of tools and subagents go to their stream)."""
    injections: PromptInjectionManager = field(default_factory=PromptInjectionManager)
    events: asyncio.Queue[RichAgentStreamEvent[Any]] = field(default_factory=asyncio.Queue)
    staged: StagedContent = field(default_factory=StagedContent)

    @property
    def idle(self) -> bool:
        """Nothing runs, and nothing waits for the next turn."""
        return (
            self.active_runs == 0
            and not self.injections.has_queued()
            and not self.injections.has_pending()
            and len(self.staged) == 0
        )


_ACTIVE: ContextVar[Mapping[int, TurnState]] = ContextVar(
    "agentpool_active_turns", default=MappingProxyType({})
)
"""Active turn of each agent (by ``id(agent)``) in the current context."""


def active_turn(agent: object) -> TurnState | None:
    """The turn of ``agent`` running in this context, if any."""
    return _ACTIVE.get().get(id(agent))


@contextmanager
def activate(agent: object, turn: TurnState) -> Generator[None]:
    """Make ``turn`` the active turn of ``agent`` in this context while the block runs.

    Restores the previous mapping by value instead of with a token, so leaving the block
    from another context (an async generator closed elsewhere) can't fail.
    """
    previous = _ACTIVE.get()
    _ACTIVE.set({**previous, id(agent): turn})
    try:
        yield
    finally:
        _ACTIVE.set(previous)
