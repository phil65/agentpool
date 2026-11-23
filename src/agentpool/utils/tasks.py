"""Task management mixin."""

from __future__ import annotations

import asyncio
from types import CoroutineType
from typing import TYPE_CHECKING, Any

import anyio

from agentpool.log import get_logger
from agentpool.utils.inspection import get_fn_name


if TYPE_CHECKING:
    from collections.abc import Coroutine
    from datetime import timedelta


logger = get_logger(__name__)


async def _after[T](delay: timedelta, coro: Coroutine[Any, Any, T]) -> T:
    await anyio.sleep(delay.total_seconds())
    return await coro


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

    def create_task[T](
        self,
        coro: Coroutine[Any, Any, T],
        *,
        name: str | None = None,
        priority: int = 0,
        delay: timedelta | None = None,
    ) -> asyncio.Task[T]:
        """Create and track a new task, started now or after ``delay``.

        A delayed task is tracked from now (it sleeps first), so waiting for the pending
        tasks (``complete_tasks``) waits for it too.

        Args:
            coro: Coroutine to run
            name: Optional name for the task (defaults to coroutine function name)
            priority: Kept for callers that pass it; tasks start when due, in no order
            delay: Optional delay before execution
        """
        task_name = name or get_fn_name(coro)
        runner = _after(delay, coro) if delay is not None else coro
        task = asyncio.create_task(runner, name=task_name)
        logger.debug("Created task", name=task.get_name(), priority=priority, delay=delay)

        def _done_callback(t: asyncio.Task[Any]) -> None:
            logger.debug("Task completed", name=t.get_name())
            self._pending_tasks.discard(t)
            if not t.cancelled() and (error := t.exception()) is not None:
                logger.error("Task failed", error=error, name=t.get_name())

        task.add_done_callback(_done_callback)
        self._pending_tasks.add(task)
        return task

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
