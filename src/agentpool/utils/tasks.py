"""Task management mixin."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import heapq
from types import CoroutineType
from typing import TYPE_CHECKING, Any

import anyio

from agentpool.log import get_logger
from agentpool.utils.inspection import get_fn_name
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import Coroutine
    from datetime import datetime, timedelta


logger = get_logger(__name__)


@dataclass(order=True)
class PrioritizedTask:
    """Task with priority and optional delay."""

    priority: int
    execute_at: datetime
    coroutine: Coroutine[Any, Any, Any] = field(compare=False)
    name: str | None = field(default=None, compare=False)


class TaskManager:
    """Mixin for managing async tasks.

    Provides utilities for:
    - Creating and tracking tasks
    - Fire-and-forget task execution
    - Running coroutines in sync context
    - Cleanup of pending tasks
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._task_queue: list[PrioritizedTask] = []  # heap queue
        self._scheduler_task: asyncio.Task[Any] | None = None

    def create_task[T](
        self,
        coro: Coroutine[Any, Any, T],
        *,
        name: str | None = None,
        priority: int = 0,
        delay: timedelta | None = None,
    ) -> asyncio.Task[T]:
        """Create and track a new task with optional priority and delay.

        Args:
            coro: Coroutine to run
            name: Optional name for the task (defaults to coroutine function name)
            priority: Priority (lower = higher priority, default 0)
            delay: Optional delay before execution
        """
        task_name = name or get_fn_name(coro)
        task = asyncio.create_task(coro, name=task_name)
        logger.debug("Created task", name=task.get_name(), priority=priority, delay=delay)

        def _done_callback(t: asyncio.Task[Any]) -> None:
            logger.debug("Task completed", name=t.get_name())
            self._pending_tasks.discard(t)
            if t.exception():
                logger.error("Task failed", error=t.exception(), name=t.get_name())

        task.add_done_callback(_done_callback)
        self._pending_tasks.add(task)

        if delay is not None:
            execute_at = get_now() + delay
            prio_task = PrioritizedTask(priority, execute_at, coro, name)
            heapq.heappush(self._task_queue, prio_task)  # Store the coro instead of task
            if not self._scheduler_task:  # Start scheduler if not running
                self._scheduler_task = asyncio.create_task(self._run_scheduler())
            task.cancel()  # Cancel the original task since we'll run it later
            return task

        return task

    async def _run_scheduler(self) -> None:
        """Run scheduled tasks when their time comes."""
        try:
            while self._task_queue:
                # Get next task without removing
                next_task = self._task_queue[0]
                now = get_now()

                if now >= next_task.execute_at:
                    # Remove and execute
                    heapq.heappop(self._task_queue)
                    # Create new task from stored coroutine
                    new_task = asyncio.create_task(next_task.coroutine, name=next_task.name)
                    self._pending_tasks.add(new_task)
                    new_task.add_done_callback(self._pending_tasks.discard)
                else:
                    # Wait until next task is due
                    await anyio.sleep((next_task.execute_at - now).total_seconds())

        except Exception:
            logger.exception("Task scheduler error")
        finally:
            self._scheduler_task = None

    def fire_and_forget(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run coroutine without waiting for result."""
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(coro)
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        except RuntimeError:
            # No running loop - use new loop
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(coro)
            finally:
                loop.close()

    def run_task_sync[T](self, coro: Coroutine[Any, Any, T]) -> T:
        """Run coroutine synchronously."""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # Running loop - use thread pool
                import concurrent.futures

                msg = "Running coroutine in Executor due to active event loop"
                name = coro.__name__ if isinstance(coro, CoroutineType) else repr(coro)
                logger.debug(msg, name=name)
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(lambda: asyncio.run(coro))
                    return future.result()

            # Existing but not running loop - use task tracking
            task = loop.create_task(coro)
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
            return loop.run_until_complete(task)
        except RuntimeError:
            # No loop - create new one
            return asyncio.run(coro)

    def run_background(
        self,
        coro: Coroutine[Any, Any, Any],
        name: str | None = None,
        priority: int = 0,
        delay: timedelta | None = None,
    ) -> None:
        """Run a coroutine in the background and track it."""
        try:
            self.create_task(coro, name=name, priority=priority, delay=delay)

        except RuntimeError:
            # No running loop - use fire_and_forget
            self.fire_and_forget(coro)

    def is_busy(self) -> bool:
        """Check if we have any tasks pending."""
        return bool(self._pending_tasks)

    async def cleanup_tasks(self) -> None:
        """Wait for all pending tasks to complete."""
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        self._pending_tasks.clear()

    async def complete_tasks(self, cancel: bool = False) -> None:
        """Wait for all pending tasks to complete."""
        if cancel:
            for task in self._pending_tasks:
                task.cancel()
        if self._pending_tasks:
            await asyncio.wait(self._pending_tasks)
