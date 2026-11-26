"""A2A server implementation following BaseServer pattern."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentpool_server.a2a_server.a2a_types import TaskData


if TYPE_CHECKING:
    from agentpool.messaging.messages import ChatMessage
    from agentpool_server.a2a_server.a2a_types import A2ARequest, TaskStatus


@dataclass
class SimpleStorage:
    """Simple in-memory storage for A2A tasks."""

    tasks: dict[str, TaskData] = field(default_factory=dict)
    """Dictionary to store task data."""
    contexts: dict[str, list[ChatMessage[Any]]] = field(default_factory=dict)
    """Dictionary to store conversation history."""

    async def store_task(self, task_id: str, task_data: A2ARequest) -> None:
        """Store a task."""
        self.tasks[task_id] = TaskData(
            id=task_id,
            status="submitted",
            data=task_data,
            result=None,
            error=None,
            context_id=task_data.get("params", {}).get("context_id"),
        )

    async def get_task(self, task_id: str) -> TaskData | None:
        """Get task data."""
        return self.tasks.get(task_id)

    async def get_task_status(self, task_id: str) -> TaskStatus:
        """Get task status."""
        task = self.tasks.get(task_id)
        if not task:
            return {"status": "not_found", "result": None, "error": None}
        return {
            "status": task["status"],
            "result": task["result"],
            "error": task["error"],
        }

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Update task status."""
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = status
            if result is not None:
                self.tasks[task_id]["result"] = result
            if error is not None:
                self.tasks[task_id]["error"] = error

    async def cancel_task(self, task_id: str) -> None:
        """Cancel a task."""
        await self.update_task_status(task_id, "cancelled")

    async def load_context(self, context_id: str) -> list[ChatMessage[Any]]:
        """Load conversation history for a context."""
        return self.contexts.get(context_id, [])

    async def update_context(
        self,
        context_id: str,
        messages: list[ChatMessage[Any]],
    ) -> None:
        """Update conversation history for a context."""
        if context_id:
            self.contexts[context_id] = messages
