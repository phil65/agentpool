"""Where a session's turns run, and what they should know about it.

A protocol server serves several sessions with the pool's agents, each session for its
own client: its own execution environment (the client's file system and terminals, its
working directory) and instructions (the working directory, the client's own system
prompt). The server makes a session's :class:`SessionEnvironment` current while it acts
for the session (``use_session_environment``); agents then run their tools in it
(``BaseAgent.env``) and native agents add its instructions to their own.

It is held in a context variable, so it reaches everything a turn runs in its task and the
tasks it starts: subagents' turns, background tasks. Work that runs in other tasks (MCP
tool calls through the tool bridge, requests of a nested ACP agent) captures it with the
turn and activates it there.

Design: ``ARCHITECTURE_SESSIONS_AGENTS.md`` §11 (session environment).
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Generator

    from exxec import ExecutionEnvironment

    from agentpool.resource_providers import ResourceProvider


@dataclass(frozen=True)
class SessionEnvironment:
    """A session's execution environment and instructions."""

    env: ExecutionEnvironment
    """Where the session's tools run (files, commands, terminals)."""

    instructions: str = ""
    """What agents should know about it: working directory, the client's instructions."""

    providers: tuple[ResourceProvider, ...] = ()
    """Resources of this session only (its client's MCP servers, its workspace's skills).

    Agents are shared by sessions: their tool managers add these to their own
    providers while the session acts (``ToolManager.providers``).
    """


_CURRENT: ContextVar[SessionEnvironment | None] = ContextVar(
    "agentpool_session_environment", default=None
)


def current_session_environment() -> SessionEnvironment | None:
    """The environment of the session acting in this context, if one is set."""
    return _CURRENT.get()


def current_session_providers() -> tuple[ResourceProvider, ...]:
    """Resource providers of the session acting in this context (none without one)."""
    environment = _CURRENT.get()
    return environment.providers if environment is not None else ()


def current_instructions() -> str:
    """Instructions of the session acting in this context (empty without one)."""
    environment = _CURRENT.get()
    return environment.instructions if environment is not None else ""


def instructions_update(started: str, current: str) -> str | None:
    """Prompt text telling a backend session its instructions changed (None if they didn't).

    For agents that give a backend session the session's instructions when it starts (a
    CLI process's system prompt, a thread's developer instructions): a change after that
    reaches the backend with the next turn's prompt, the instructions in full.
    """
    if current == started:
        return None
    body = current or "The session's instructions were cleared."
    return f"<session-instructions>\n{body}\n</session-instructions>"


@contextmanager
def use_session_environment(environment: SessionEnvironment | None) -> Generator[None]:
    """Make ``environment`` current while the block runs (None: keep the current one).

    Restores the previous value by value instead of with a token, so leaving the block
    from another context (an async generator closed elsewhere) can't fail.
    """
    if environment is None:
        yield
        return
    previous = _CURRENT.get()
    _CURRENT.set(environment)
    try:
        yield
    finally:
        _CURRENT.set(previous)
