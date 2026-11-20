"""Agent tasks (subagent runs, background commands) of a session, listed as OpenCode jobs.

Tasks come from the agent's ``Task*Event``s. OpenCode models a subagent job with a child
session; AgentPool shows subagent activity inside the parent session (on the delegating
tool call), so the job's ``childSessionID`` is the session itself: interrupting it stops
the subagent along with the turn.

A background task can end after its turn: the agent then reports it as a background event
(``BaseAgent.background_event``), which the server routes to the session's tracker.

Like OpenCode's own jobs, a task is listed until its result has been consumed: until the
end of the execution it finished in, or of the turn the agent takes to react to it
(Claude Code does so on its own). Clients such as ``opencode run`` wait for every listed
job, so finished tasks must not linger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from agentpool.agents.events import (
    SubAgentEvent,
    TaskFinishedEvent,
    TaskStartedEvent,
    TaskUpdatedEvent,
)
from agentpool.utils.time_utils import now_ms
from opencode_v2_sdk.models import JobShell, JobSubagent, JobTime


if TYPE_CHECKING:
    from agentpool.agents.events import RichAgentStreamEvent, StreamWithCommandsEvent


type JobStatus = Literal["running", "completed", "error", "cancelled"]


@dataclass
class _Task:
    start: TaskStartedEvent
    description: str
    started: int
    status: JobStatus = "running"
    completed: int | None = None


@dataclass
class TaskTracker:
    """Tasks of one session."""

    session_id: str
    tasks: dict[str, _Task] = field(default_factory=dict)

    def observe(self, event: StreamWithCommandsEvent[Any] | RichAgentStreamEvent[Any]) -> None:
        """Record task events (also those of subagents)."""
        match event:
            case TaskStartedEvent(task_id=task_id, description=description):
                self.tasks[task_id] = _Task(start=event, description=description, started=now_ms())
            case TaskUpdatedEvent(task_id=task_id, description=str() as description) if (
                task := self.tasks.get(task_id)
            ) is not None:
                task.description = description
            case TaskFinishedEvent(task_id=task_id, status=status) if (
                task := self.tasks.get(task_id)
            ) is not None:
                task.status = "error" if status == "failed" else status
                task.completed = now_ms()
            case SubAgentEvent(event=inner):
                self.observe(inner)
            case _:
                pass

    def consume_finished(self) -> None:
        """Drop finished tasks: an execution of the session has seen their results."""
        self.tasks = {k: task for k, task in self.tasks.items() if task.status == "running"}

    def jobs(self) -> list[JobShell | JobSubagent]:
        """The tasks as OpenCode jobs."""
        jobs: list[JobShell | JobSubagent] = []
        for task_id, task in self.tasks.items():
            time = JobTime(started=task.started, completed=task.completed)
            if task.start.kind == "shell":
                job: JobShell | JobSubagent = JobShell(
                    id=task_id,
                    session_id=self.session_id,
                    status=task.status,
                    title=task.description,
                    time=time,
                    shell_id=task_id,
                    command=task.description,
                )
            else:
                job = JobSubagent(
                    id=task_id,
                    session_id=self.session_id,
                    status=task.status,
                    title=task.description,
                    time=time,
                    child_session_id=self.session_id,
                    agent=task.start.agent_type or task.start.kind,
                    description=task.description,
                )
            jobs.append(job)
        return jobs
