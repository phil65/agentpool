"""AggregatingServer for managing multiple servers with shared pool."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Self

from agentpool.log import get_logger
from agentpool_server.base import BaseServer


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from agentpool import AgentPool

# Type-safe server status literals
ServerStatus = Literal["not_initialized", "initialized", "running", "failed", "stopped"]


@dataclass(frozen=True)
class ServerInfo:
    """Type-safe server information."""

    name: str
    server_type: type[BaseServer]
    status: ServerStatus


logger = get_logger(__name__)


class AggregatingServer(BaseServer):
    """Server that manages multiple protocol servers sharing one AgentPool.

    Coordinates multiple server instances (AG-UI, A2A, MCP, etc.) as a single unit.
    All servers share the same AgentPool and are started/stopped together.

    This is useful when you want to expose the same pool of agents via multiple
    protocols simultaneously (e.g., AG-UI for web clients, A2A for agent-to-agent
    communication).

    Example:
        ```python
        async with AgentPool(config) as pool:
            servers = [
                AGUIServer(pool, port=8002),
                A2AServer(pool, port=8001),
            ]
            async with AggregatingServer(pool, servers) as agg:
                async with agg.run_context():
                    # Both servers running, sharing the same pool
                    await asyncio.Event().wait()
        ```
    """

    def __init__(
        self,
        pool: AgentPool[Any],
        servers: Sequence[BaseServer],
        *,
        name: str | None = None,
        raise_exceptions: bool = False,
    ) -> None:
        """Initialize aggregating server.

        Args:
            pool: AgentPool to be shared by all servers
            servers: Sequence of servers to aggregate
            name: Server name for logging (auto-generated if None)
            raise_exceptions: Whether to raise exceptions during server start
        """
        if not servers:
            raise ValueError("At least one server must be provided")

        super().__init__(pool, name=name, raise_exceptions=raise_exceptions)

        self.servers = list(servers)
        self.exit_stack = AsyncExitStack()
        self._initialized_servers: list[BaseServer] = []

    async def __aenter__(self) -> Self:
        """Initialize aggregating server and all child servers."""
        await super().__aenter__()

        self.log.info("Initializing aggregated servers", count=len(self.servers))

        try:
            for server in self.servers:
                try:
                    initialized_server = await self.exit_stack.enter_async_context(server)
                    self._initialized_servers.append(initialized_server)
                    self.log.info("Initialized server", server_name=server.name)
                except Exception:
                    self.log.exception("Failed to initialize server", server_name=server.name)
                    if self.raise_exceptions:
                        raise

            if not self._initialized_servers:
                raise RuntimeError("No servers were successfully initialized")  # noqa: TRY301

            self.log.info(
                "All servers initialized",
                successful=len(self._initialized_servers),
                failed=len(self.servers) - len(self._initialized_servers),
            )

        except Exception:
            await self.exit_stack.aclose()
            raise

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Cleanup all servers and base server resources."""
        self.log.info("Shutting down aggregated servers")

        await self.exit_stack.aclose()
        self._initialized_servers.clear()

        await super().__aexit__(exc_type, exc_val, exc_tb)

        self.log.info("Aggregated servers shutdown complete")

    async def _start_async(self) -> None:
        """Start all initialized servers concurrently."""
        if not self._initialized_servers:
            self.log.warning("No initialized servers to start")
            return

        self.log.info("Starting aggregated servers", count=len(self._initialized_servers))

        server_tasks: list[tuple[BaseServer, asyncio.Task[None] | None]] = []

        for server in self._initialized_servers:
            try:
                server.start_background()
                server_tasks.append((server, server._server_task))
                self.log.info("Started server in background", server=server.name)
            except Exception:
                self.log.exception("Failed to start server", server=server.name)
                if self.raise_exceptions:
                    for started_server, _ in server_tasks:
                        try:
                            started_server.stop()
                        except Exception:
                            self.log.exception(
                                "Error stopping server", server_name=started_server.name
                            )
                    raise

        if not server_tasks:
            raise RuntimeError("No servers were successfully started")

        self.log.info(
            "All servers started",
            successful=len(server_tasks),
            failed=len(self._initialized_servers) - len(server_tasks),
        )

        tasks = [task for _, task in server_tasks if task is not None]
        if tasks:
            await self._wait_for_tasks(tasks, server_tasks)

    async def _wait_for_tasks(
        self,
        tasks: list[asyncio.Task[None]],
        server_tasks: Sequence[tuple[BaseServer, asyncio.Task[None] | None]],
    ) -> None:
        """Wait for server tasks and handle shutdown."""
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                if task.cancelled():
                    self.log.info("Server task cancelled")
                elif task.exception():
                    self.log.error("Server task failed", error=task.exception())
                else:
                    self.log.info("Server task completed")

            # Stop all other servers gracefully
            for server, _ in server_tasks:
                try:
                    server.stop()
                except Exception:
                    self.log.exception("Error stopping server")

            if pending:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        except asyncio.CancelledError:
            for server, _ in server_tasks:
                try:
                    server.stop()
                except Exception:
                    self.log.exception("Error stopping server during cancellation")
            raise

    def add_server(self, server: BaseServer) -> None:
        """Add a server to the aggregation.

        Args:
            server: Server to add

        Raises:
            RuntimeError: If aggregating server is currently running
        """
        if self.is_running:
            raise RuntimeError("Cannot add server while aggregating server is running")

        self.servers.append(server)
        self.log.info("Added server to aggregation", server=server.name)

    def remove_server(self, server: BaseServer) -> None:
        """Remove a server from the aggregation.

        Args:
            server: Server to remove

        Raises:
            RuntimeError: If aggregating server is currently running
            ValueError: If server is not in aggregation
        """
        if self.is_running:
            raise RuntimeError("Cannot remove server while aggregating server is running")

        try:
            self.servers.remove(server)
            self.log.info("Removed server from aggregation", server=server.name)
        except ValueError as e:
            raise ValueError(f"Server {server.name} not found in aggregation") from e

    def get_server(self, name: str) -> BaseServer | None:
        """Get a server by name from the aggregation.

        Args:
            name: Server name to find

        Returns:
            Server instance or None if not found
        """
        all_servers = self.servers + self._initialized_servers
        for server in all_servers:
            if server.name == name:
                return server
        return None

    def list_servers(self) -> list[ServerInfo]:
        """List all servers in the aggregation with their status.

        Returns:
            List of type-safe ServerInfo objects
        """
        return [
            ServerInfo(
                name=server.name,
                server_type=type(server),
                status=self._get_server_status(server),
            )
            for server in self.servers
        ]

    def get_server_status(self) -> dict[str, ServerStatus]:
        """Get status of all servers.

        Returns:
            Dict mapping server names to their type-safe status
        """
        return {server.name: self._get_server_status(server) for server in self.servers}

    def _get_server_status(self, server: BaseServer) -> ServerStatus:
        """Get type-safe status for a specific server."""
        if server in self._initialized_servers:
            return "running" if server.is_running else "initialized"
        return "not_initialized"

    @property
    def initialized_server_count(self) -> int:
        """Number of successfully initialized servers."""
        return len(self._initialized_servers)

    @property
    def running_server_count(self) -> int:
        """Number of currently running servers."""
        return sum(1 for server in self._initialized_servers if server.is_running)

    def __repr__(self) -> str:
        """String representation of aggregating server."""
        return (
            f"AggregatingServer(name={self.name}, "
            f"servers={len(self.servers)}, "
            f"initialized={len(self._initialized_servers)}, "
            f"running={self.running_server_count})"
        )
