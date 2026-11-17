"""Task Supervisor."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from collections.abc import Coroutine


__all__ = ["TaskSupervisor"]

logger = structlog.get_logger(__name__)
ErrorHandler = Callable[[asyncio.Task[Any], BaseException], None]


class TaskSupervisor:
    """Track background tasks and provide graceful shutdown semantics.

    Inspired by fasta2a's task manager, this supervisor keeps a registry of
    asyncio tasks created for request handling so they can be cancelled and
    awaited reliably when the connection closes.
    """

    def __init__(self, *, source: str, error_handlers: list[ErrorHandler] | None = None) -> None:
        self._source = source
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._error_handlers = error_handlers or []

    @property
    def closed(self) -> bool:
        """Whether the supervisor stopped accepting new tasks."""
        return self._closed

    def add_error_handler(self, handler: ErrorHandler) -> None:
        self._error_handlers.append(handler)

    def create(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
        on_error: ErrorHandler | None = None,
    ) -> asyncio.Task[Any]:
        if self._closed:
            coroutine.close()  # Avoid a "coroutine was never awaited" warning
            raise RuntimeError(f"TaskSupervisor for {self._source} already closed")
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(lambda t: self._on_done(t, on_error))
        return task

    def _on_done(self, task: asyncio.Task[Any], on_error: ErrorHandler | None) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            handled = False
            if on_error is not None:
                try:
                    on_error(task, exc)
                    handled = True
                except Exception:
                    logger.exception("Error in task-specific error handler", source=self._source)
            if not handled:
                for handler in self._error_handlers:
                    try:
                        handler(task, exc)
                        handled = True
                    except Exception:
                        logger.exception("Error in supervisor error handler", source=self._source)
            if not handled:
                logger.exception("Unhandled error in task", source=self._source)

    async def shutdown(self) -> None:
        """Cancel all tracked tasks and wait for them to finish.

        The calling task is left alone: a handler may close its own connection, and
        cancelling and awaiting itself would end in a self-await cancellation cycle.
        """
        self._closed = True
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        # Failures were already routed to the error handlers by `_on_done`.
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.difference_update(tasks)
