"""Base classes for protocol bridge servers."""

import abc
from typing import TYPE_CHECKING, Any, Self

from agentpool.log import get_logger


if TYPE_CHECKING:
    from types import TracebackType

    from agentpool import AgentPool


logger = get_logger(__name__)


class ServerBridge(abc.ABC):
    """Base class for AgentPool bridge servers.

    Provides common lifecycle management, context manager protocol,
    and running state handling for servers that bridge AgentPool agents
    to external protocols (ACP, OpenAI API, MCP, etc.).
    """

    def __init__(self, pool: AgentPool[Any], **kwargs: Any) -> None:
        """Initialize the server bridge.

        Args:
            pool: Agent pool to expose via the protocol
            **kwargs: Additional configuration options
        """
        self._pool = pool
        self._running = False
        self._server_config = kwargs

    @property
    def is_running(self) -> bool:
        """Whether the server is currently running."""
        return self._running

    @property
    def pool(self) -> AgentPool[Any]:
        """The underlying agent pool."""
        return self._pool

    async def run(self) -> None:
        """Run the server.

        Template method that handles common lifecycle management
        and delegates to subclass-specific _run() implementation.

        Raises:
            RuntimeError: If server is already running
        """
        if self._running:
            raise RuntimeError("Server is already running")

        logger.info("Starting server", name=self.__class__.__name__)
        self._running = True

        try:
            await self._run()
        except Exception:
            logger.exception("Server error")
            raise
        finally:
            self._running = False
            logger.info("Server stopped", name=self.__class__.__name__)

    @abc.abstractmethod
    async def _run(self) -> None:
        """Run the server implementation.

        Subclasses must implement this method to handle their
        specific protocol setup and serving logic.
        """
        ...

    async def shutdown(self) -> None:
        """Shutdown the server.

        Default implementation just sets running state to False.
        Subclasses can override for custom cleanup logic.
        """
        if self._running:
            logger.info("Shutting down server", name=self.__class__.__name__)
            self._running = False

    async def __aenter__(self) -> Self:
        """Async context manager entry.

        Default implementation does nothing. Subclasses can override
        to perform initialization before the server starts.

        Returns:
            Self for fluent interface
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit.

        Default implementation calls shutdown() if server is running.
        Subclasses can override for custom cleanup logic.
        """
        if self._running:
            await self.shutdown()

    def __repr__(self) -> str:
        """String representation of the server."""
        status = "running" if self._running else "stopped"
        pool_info = f"pool-{len(self._pool.all_agents)}-agents"
        return f"{self.__class__.__name__}({pool_info}, {status})"
