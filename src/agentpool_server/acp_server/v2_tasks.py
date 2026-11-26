"""ACP v2 ``tool_call/cancel`` for tool calls whose task can be stopped.

A tool call that started a stoppable task (a subagent run, a background command) offers
``capabilities.cancel`` while the task runs, also after the tool call itself completed (a
background task outlives it). Cancelling it stops the task (``BaseAgent.stop_task``).
"""

from typing import TYPE_CHECKING, Any

from aclientp.schema import v2

from agentpool.agents.events import TaskFinishedEvent, TaskStartedEvent


if TYPE_CHECKING:
    from agentpool.agents.events import RichAgentStreamEvent


class CancellableToolCalls:
    """The tool calls of a session that can be cancelled: their task, by tool call id."""

    def __init__(self) -> None:
        self._tasks: dict[str, str] = {}

    def task_of(self, tool_call_id: str) -> str | None:
        """The task cancelling ``tool_call_id`` stops, if it can be cancelled."""
        return self._tasks.get(tool_call_id)

    def updates(self, event: RichAgentStreamEvent[Any]) -> list[v2.SessionUpdate]:
        """The capability changes an event brings (cancel offered, or withdrawn)."""
        match event:
            case TaskStartedEvent(tool_call_id=str(tool_call_id), task_id=task_id, stoppable=True):
                self._tasks[tool_call_id] = task_id
                cancel = v2.ToolCallCapabilities(cancel=v2.ToolCallCancelCapabilities())
                return [v2.ToolCallSessionUpdate(tool_call_id=tool_call_id, capabilities=cancel)]
            case TaskFinishedEvent(task_id=task_id):
                ended = [tc for tc, task in self._tasks.items() if task == task_id]
                for tool_call_id in ended:
                    del self._tasks[tool_call_id]
                return [
                    v2.ToolCallSessionUpdate(tool_call_id=tool_call_id, capabilities=None)
                    for tool_call_id in ended
                ]
            case _:
                return []
