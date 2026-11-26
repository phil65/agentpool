"""Tasks an agent's turns started: subagent runs and background commands still running.

Agents report them as ``TaskStartedEvent`` / ``TaskUpdatedEvent`` / ``TaskFinishedEvent``
(a background task may finish turns later, as a background event). Each session's runtime
keeps the running ones (:class:`RunningTask`), so users can list, stop and read them
(``BaseAgent.running_tasks``, ``stop_task``, ``task_output``).
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentpool.agents.events import TaskFinishedEvent, TaskStartedEvent, TaskUpdatedEvent
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.events.events import TaskKind


@dataclass(kw_only=True)
class RunningTask:
    """A task that started and hasn't finished."""

    task_id: str
    kind: TaskKind
    description: str
    session_id: str
    """The session whose turn started it."""
    background: bool = False
    """Whether the turn goes on without waiting for it."""
    tool_call_id: str | None = None
    """The tool call that started it."""
    started_at: datetime = field(default_factory=get_now)
    message: str | None = None
    """Its latest progress summary."""


@dataclass(frozen=True, kw_only=True)
class TaskOutput:
    """What a task has written so far."""

    output: str
    total_bytes: int
    """The size of all of its output."""
    truncated: bool = False
    """Whether ``output`` is only part of it."""


@dataclass(frozen=True, kw_only=True)
class LocalTask:
    """A task agentpool runs itself (e.g. a subagent in the background): stoppable here."""

    task: asyncio.Task[Any]
    read_output: Callable[[], Awaitable[TaskOutput]]


def track_task(
    tasks: dict[str, RunningTask], event: RichAgentStreamEvent[Any], session_id: str
) -> None:
    """Record a task event of ``session_id`` in its running tasks (by task id)."""
    match event:
        case TaskStartedEvent():
            tasks[event.task_id] = RunningTask(
                task_id=event.task_id,
                kind=event.kind,
                description=event.description,
                session_id=session_id,
                background=event.background,
                tool_call_id=event.tool_call_id,
            )
        case TaskUpdatedEvent(task_id=task_id) if (task := tasks.get(task_id)) is not None:
            if event.description is not None:
                task.description = event.description
            if event.message is not None:
                task.message = event.message
            if event.background is not None:
                task.background = event.background
        case TaskFinishedEvent(task_id=task_id):
            tasks.pop(task_id, None)
        case _:
            pass
