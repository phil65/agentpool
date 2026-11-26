"""Base server class for AgentPool servers."""

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Self

from agentpool.log import get_logger
from agentpool.utils.tasks import TaskManager


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from types import TracebackType

    from agentpool import AgentPool

logger = get_logger(__name__)


class BaseServer:
    """Base class for all AgentPool servers.

    Provides standardized interface for server lifecycle management:
    - async def _start_async() - blocking server execution (implemented by subclasses)
    - def start_background() - non-blocking server start via background task
    - def stop() - stop background server task
    - async with run_context() - automatic server start/stop management

    Features:
    - Centralized task management via TaskManager
    - Configurable exception handling via raise_exceptions parameter
    - Automatic pool lifecycle management
    - Background task lifecycle management
    """

    def __init__(
        self,
        pool: AgentPool[Any],
        *,
        name: str | None = None,
        raise_exceptions: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize base server with agent pool.

        Args:
            pool: AgentPool containing available agents
            name: Optional Server name (auto-generated from class name if None)
            raise_exceptions: Whether to raise exceptions during server start
            **kwargs: Additional arguments (for subclass compatibility)
        """
        self.pool = pool
        self.name = name or f"{self.__class__.__name__}-{id(self):x}"
        self.raise_exceptions = raise_exceptions
        self.task_manager = TaskManager()
        self._server_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()
        self.log = logger.bind(server_name=self.name)

    async def __aenter__(self) -> Self:
        """Enter async context and initialize server resources (pool, etc.)."""
        await self.pool.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context and cleanup server resources."""
        # Cleanup any pending tasks
        await self.task_manager.cleanup_tasks()
        # Cleanup pool
        await self.pool.__aexit__(exc_type, exc_val, exc_tb)

    async def _start_async(self) -> None:
        """Start the server (blocking async - runs until stopped).

        This method must be implemented by subclasses and should run
        the server until it's stopped or encounters an error.

        This is the internal implementation that subclasses override.
        The public start() method handles exception management.
        """
        raise NotImplementedError("Subclasses must implement _start_async()")

    async def start(self) -> None:
        """Start the server (blocking async - runs until stopped).

        This is the public interface that handles exception management
        based on the raise_exceptions setting. Subclasses should implement
        _start_async() instead of overriding this method.
        """
        try:
            self.log.info("Starting server")
            await self._start_async()
        except Exception as e:
            if self.raise_exceptions:
                raise
            self.log.exception("Server error", exc_info=e)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Shutdown server resources.

        This method can be overridden by subclasses to add specific
        cleanup logic. The base implementation handles task cleanup.
        """
        try:
            await self.task_manager.cleanup_tasks()
        except Exception:
            self.log.exception("Error during server shutdown")
        finally:
            self._shutdown_event.set()
            self.log.info("Server shutdown complete")

    def start_background(self) -> None:
        """Start server in background task (non-blocking).

        Creates a background task that runs start() method.
        Server will run in the background until stop() is called.
        """
        if self._server_task is not None and not self._server_task.done():
            raise RuntimeError("Server is already running in background")

        self._shutdown_event.clear()
        # Not in task_manager: shutdown() awaits its tasks and runs in this one
        self._server_task = asyncio.create_task(self._run_with_shutdown(), name=f"{self.name}-task")

    async def _run_with_shutdown(self) -> None:
        """Internal wrapper that handles shutdown signaling."""
        try:
            await self.start()
        finally:
            self._shutdown_event.set()

    def stop(self) -> None:
        """Stop the background server task (non-blocking)."""
        if self._server_task is not None and not self._server_task.done():
            self._server_task.cancel()

    async def wait_until_stopped(self) -> None:
        """Wait until the server stops (either by stop() or natural completion)."""
        if self._server_task is None:
            return

        # Wait for either task completion or shutdown event
        await self._shutdown_event.wait()

        # Ensure task is cleaned up
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()

        if self._server_task:
            await asyncio.gather(self._server_task, return_exceptions=True)
            self._server_task = None

    @property
    def is_running(self) -> bool:
        """Check if server is currently running in background."""
        return self._server_task is not None and not self._server_task.done()

    @asynccontextmanager
    async def run_context(self) -> AsyncGenerator[None]:
        """Async context manager for automatic server start/stop.

        Starts the server in background when entering context,
        automatically stops it when exiting context.

        Example:
            async with server:  # Initialize resources
                async with server.run_context():  # Start server
                    # Server is running in background
                    await do_other_work()
                # Server automatically stopped
        """
        self.start_background()
        try:
            yield
        finally:
            self.stop()
            await self.wait_until_stopped()
