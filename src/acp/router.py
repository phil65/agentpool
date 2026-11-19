"""Route an agent's client connection to its ACP v1 or v2 implementation.

The router owns the connection until a valid ``initialize`` request arrives. Before
that, notifications are ignored, other requests get ``-32600``, and a JSON-RPC batch
gets a single ``-32600``. ``initialize`` selects the highest configured version at or
below the requested one (v1-only endpoints keep the legacy fallback to v1); the
selected implementation then handles ``initialize`` and every later message. A
connection is never re-routed.

Mirrors ``AgentProtocolRouter`` of the pinned TypeScript SDK
(``schema/acp/references.toml``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from acp.agent.connection import AgentSideConnection as V1AgentSideConnection
from acp.connection import Connection
from acp.exceptions import RequestError
from acp.v2.agent import AgentSideConnection as V2AgentSideConnection


if TYPE_CHECKING:
    from collections.abc import Callable

    from anyio.abc import ByteReceiveStream, ByteSendStream

    from acp.agent.protocol import Agent as V1Agent
    from acp.connection import MethodHandler, StreamObserver
    from acp.v2.agent import Agent as V2Agent


class RoutedAgentConnection:
    """A client connection served by the v1 or the v2 agent, chosen on ``initialize``."""

    def __init__(
        self,
        input_stream: ByteSendStream,
        output_stream: ByteReceiveStream,
        *,
        v1: Callable[[V1AgentSideConnection], V1Agent] | V1Agent | None = None,
        v2: Callable[[V2AgentSideConnection], V2Agent] | V2Agent | None = None,
        observers: list[StreamObserver] | None = None,
    ) -> None:
        """Start routing a connection.

        Args:
            input_stream: Stream to the client.
            output_stream: Stream from the client.
            v1: The v1 agent, or a factory receiving its connection.
            v2: The v2 agent, or a factory receiving its connection.
            observers: Observers of the raw messages.
        """
        if v1 is None and v2 is None:
            msg = "configure at least one of the v1 and v2 agents"
            raise ValueError(msg)
        self._v1 = v1
        self._v2 = v2
        self._target: MethodHandler | None = None
        self.protocol_version: int | None = None
        """The selected protocol version; ``None`` until ``initialize`` arrived."""
        self.v1_side: V1AgentSideConnection | None = None
        self.v2_side: V2AgentSideConnection | None = None
        self._conn = Connection(
            self._route,
            input_stream,
            output_stream,
            observers=observers,
            reject_batches=True,
        )

    @property
    def connection(self) -> Connection:
        """The underlying JSON-RPC connection."""
        return self._conn

    async def _route(self, method: str, params: Any, is_notification: bool) -> Any:
        if self._target is not None:
            return await self._target(method, params, is_notification)
        if is_notification:
            return None
        if method != "initialize":
            details = f"the connection must be initialized before {method!r}"
            raise RequestError.invalid_request({"details": details})
        self._target = self._select(_requested_version(params))
        return await self._target(method, params, is_notification)

    def _select(self, requested: int) -> MethodHandler:
        if self._v2 is not None and requested >= V2AgentSideConnection.PROTOCOL_VERSION:
            side = V2AgentSideConnection.on_connection(self._conn, self._v2)
            self.v2_side, self.protocol_version = side, side.PROTOCOL_VERSION
            return side.handle
        if self._v1 is not None:
            # v1 negotiates down itself; keep v1's handling of batches (skipped).
            self._conn.reject_batches = False
            self.v1_side, handler = V1AgentSideConnection.on_connection(self._conn, self._v1)
            self.protocol_version = 1
            return handler
        supported = [V2AgentSideConnection.PROTOCOL_VERSION]
        raise RequestError.unsupported_protocol_version(requested, supported)

    async def close(self) -> None:
        """Close the connection."""
        await self._conn.close()

    async def wait_closed(self) -> None:
        """Wait until the client disconnected or the connection was closed."""
        await self._conn.wait_closed()


def _requested_version(params: Any) -> int:
    version = params.get("protocolVersion") if isinstance(params, dict) else None
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise RequestError.invalid_params({"protocolVersion": version})
    return version
