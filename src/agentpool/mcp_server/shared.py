"""MCP server connections shared by reference count (the client servers of sessions)."""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentpool.log import get_logger
from agentpool.resource_providers.mcp_provider import MCPResourceProvider


if TYPE_CHECKING:
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)


@dataclass
class _Connection:
    connecting: asyncio.Task[MCPResourceProvider]
    refs: int = 0


class SharedMCPConnections:
    """Connections to MCP servers, shared by everyone asking for the same server.

    ACP clients send their MCP servers with every ``session/new`` and ``session/load``.
    The servers belong to the session, but sessions asking for the same server
    (same configuration) share one connection (one process for a stdio server). It is
    closed when the last session using it releases it.
    """

    def __init__(self, name: str = "session_mcp") -> None:
        self.name = name
        self._connections: dict[str, _Connection] = {}

    def __len__(self) -> int:
        return len(self._connections)

    @staticmethod
    def key(config: MCPServerConfig) -> str:
        """Identity of a server: its full configuration."""
        return config.model_dump_json()

    async def acquire(self, config: MCPServerConfig) -> MCPResourceProvider:
        """A connected provider for ``config``, shared with others asking for it.

        Every successful call must be paired with a ``release``.

        Raises:
            RuntimeError: The server could not be connected
        """
        key = self.key(config)
        if (connection := self._connections.get(key)) is None:
            task = asyncio.create_task(self._connect(config), name=f"{self.name}-connect")
            connection = self._connections[key] = _Connection(connecting=task)
        connection.refs += 1
        try:
            return await asyncio.shield(connection.connecting)
        except BaseException:
            await self._unref(key, connection)
            raise

    async def release(self, provider: MCPResourceProvider) -> None:
        """Give up one use of ``provider``; the last one closes the connection."""
        key = self.key(provider.server)
        if (connection := self._connections.get(key)) is not None:
            await self._unref(key, connection)

    async def close(self) -> None:
        """Close every connection, whoever still uses it."""
        connections = list(self._connections.values())
        self._connections.clear()
        for connection in connections:
            await self._close(connection)

    async def _unref(self, key: str, connection: _Connection) -> None:
        connection.refs -= 1
        if connection.refs > 0:
            return
        if self._connections.get(key) is connection:
            del self._connections[key]
        await self._close(connection)

    async def _close(self, connection: _Connection) -> None:
        task = connection.connecting
        task.cancel()  # Stops a connect still running (no effect on a finished one)
        await asyncio.wait([task])
        if task.cancelled() or task.exception() is not None:
            return
        try:
            await task.result().__aexit__(None, None, None)
        except Exception:
            logger.exception("Failed to close shared MCP server")

    async def _connect(self, config: MCPServerConfig) -> MCPResourceProvider:
        provider = MCPResourceProvider(
            server=config, name=f"{self.name}_{config.name or config.client_id}"
        )
        await provider.__aenter__()
        logger.info("Connected shared MCP server", server=config.client_id)
        return provider
