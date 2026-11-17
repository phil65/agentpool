"""Transport abstractions for ACP agents.

This module provides transport configuration classes and utilities for running
ACP agents over different transports (stdio, WebSocket, etc.).
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
import subprocess
from typing import TYPE_CHECKING, Any, Literal, assert_never

import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Mapping
    from pathlib import Path

    from anyio.abc import Process
    from websockets.asyncio.server import ServerConnection

    from acp.agent.connection import AgentSideConnection
    from acp.agent.protocol import Agent

logger = logging.getLogger(__name__)


# =============================================================================
# Transport Configuration Classes
# =============================================================================


@dataclass
class StdioTransport:
    """Configuration for stdio transport.

    This is the default transport for ACP agents, communicating over
    stdin/stdout streams.
    """


@dataclass
class WebSocketTransport:
    """Configuration for WebSocket server transport.

    Runs an ACP agent as a WebSocket server that accepts client connections.
    Each client connection gets its own agent instance.

    Attributes:
        host: Host to bind the WebSocket server to.
        port: Port for the WebSocket server.
    """

    host: str = "localhost"
    port: int = 8765


@dataclass
class StreamTransport:
    """Configuration for custom stream transport.

    Allows passing pre-created streams for the agent to use.

    Attributes:
        reader: Stream to read incoming messages from.
        writer: Stream to write outgoing messages to.
    """

    reader: ByteReceiveStream
    writer: ByteSendStream


# Type alias for all supported transports
Transport = StdioTransport | WebSocketTransport | StreamTransport | Literal["stdio", "websocket"]


# =============================================================================
# Transport Runner
# =============================================================================


async def serve(
    agent: Agent | Callable[[AgentSideConnection], Agent],
    transport: Transport = "stdio",
    *,
    shutdown_event: asyncio.Event | None = None,
    debug_file: str | None = None,
    **kwargs: Any,
) -> None:
    """Run an ACP agent with the specified transport.

    This is the main entry point for running ACP agents. It handles transport
    setup and lifecycle management automatically.

    Args:
        agent: An Agent implementation or a factory callable that takes
            an AgentSideConnection and returns an Agent.
        transport: Transport configuration. Can be:
            - "stdio" or StdioTransport(): Use stdin/stdout
            - "websocket" or WebSocketTransport(...): Run WebSocket server
            - StreamTransport(...): Use custom streams
        shutdown_event: Optional event to signal shutdown. If not provided,
            runs until cancelled.
        debug_file: Optional file path for debug message logging.
        **kwargs: Additional keyword arguments passed to AgentSideConnection.

    Example:
        ```python
        # Stdio (default)
        await serve(MyAgent())

        # WebSocket server
        await serve(MyAgent(), WebSocketTransport(host="0.0.0.0", port=9000))

        # With shutdown control
        shutdown = asyncio.Event()
        task = asyncio.create_task(serve(MyAgent(), shutdown_event=shutdown))
        # ... later ...
        shutdown.set()
        await task
        ```
    """
    # Normalize string shortcuts to config objects
    match transport:
        case "stdio":
            transport = StdioTransport()
        case "websocket":
            transport = WebSocketTransport()

    # Dispatch to appropriate runner
    match transport:
        case StdioTransport():
            await _serve_stdio(agent, shutdown_event, debug_file, **kwargs)
        case WebSocketTransport(host=host, port=port):
            await _serve_websocket(agent, host, port, shutdown_event, debug_file, **kwargs)
        case StreamTransport(reader=reader, writer=writer):
            await _serve_streams(agent, reader, writer, shutdown_event, debug_file, **kwargs)
        case _ as unreachable:
            assert_never(unreachable)


async def _run_until_closed(
    conn: AgentSideConnection, shutdown_event: asyncio.Event | None
) -> None:
    """Keep the connection open until the peer disconnects, shutdown is signalled or cancelled."""
    waiters: set[asyncio.Task[Any]] = {asyncio.create_task(conn.wait_closed())}
    if shutdown_event is not None:
        waiters.add(asyncio.create_task(shutdown_event.wait()))
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        pass
    finally:
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        await conn.close()


async def _serve_stdio(
    agent: Agent | Callable[[AgentSideConnection], Agent],
    shutdown_event: asyncio.Event | None,
    debug_file: str | None,
    **kwargs: Any,
) -> None:
    """Run agent over stdio."""
    from acp.agent.connection import AgentSideConnection
    from acp.stdio import stdio_streams

    agent_factory = _ensure_factory(agent)
    reader, writer = await stdio_streams()

    conn = AgentSideConnection(agent_factory, writer, reader, debug_file=debug_file, **kwargs)
    await _run_until_closed(conn, shutdown_event)


async def _serve_streams(
    agent: Agent | Callable[[AgentSideConnection], Agent],
    reader: ByteReceiveStream,
    writer: ByteSendStream,
    shutdown_event: asyncio.Event | None,
    debug_file: str | None,
    **kwargs: Any,
) -> None:
    """Run agent over custom streams."""
    from acp.agent.connection import AgentSideConnection

    agent_factory = _ensure_factory(agent)
    conn = AgentSideConnection(agent_factory, writer, reader, debug_file=debug_file, **kwargs)
    await _run_until_closed(conn, shutdown_event)


async def _serve_websocket(
    agent: Agent | Callable[[AgentSideConnection], Agent],
    host: str,
    port: int,
    shutdown_event: asyncio.Event | None,
    debug_file: str | None,
    **kwargs: Any,
) -> None:
    """Run agent as WebSocket server."""
    import websockets

    from acp.agent.connection import AgentSideConnection

    agent_factory = _ensure_factory(agent)
    shutdown = shutdown_event or asyncio.Event()
    connections: list[AgentSideConnection] = []

    async def handle_client(websocket: ServerConnection) -> None:
        """Handle a single WebSocket client connection."""
        logger.info("WebSocket client connected")

        # Create stream adapters for WebSocket
        ws_reader = _WebSocketReadStream(websocket)
        ws_writer = _WebSocketWriteStream(websocket)

        conn = AgentSideConnection(
            agent_factory, ws_writer, ws_reader, debug_file=debug_file, **kwargs
        )
        connections.append(conn)
        try:
            # The reader turns a closed socket into EOF, which closes the connection.
            await _run_until_closed(conn, shutdown)
        finally:
            connections.remove(conn)
            logger.info("WebSocket client disconnected")

    logger.info("Starting WebSocket server on ws://%s:%d", host, port)
    async with websockets.serve(handle_client, host, port):
        logger.info("WebSocket server running on ws://%s:%d", host, port)
        await shutdown.wait()

    # Clean up remaining connections
    for conn in connections:
        await conn.close()


class _WebSocketReadStream(ByteReceiveStream):
    """Adapter to read from WebSocket as a ByteReceiveStream."""

    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket
        self._buffer = b""

    async def receive(self, max_bytes: int = 65536) -> bytes:
        # If we have buffered data, return it
        if self._buffer:
            data = self._buffer[:max_bytes]
            self._buffer = self._buffer[max_bytes:]
            return data

        # Read from WebSocket
        try:
            message: str | bytes = await self._websocket.recv()
            if isinstance(message, str):
                message = message.encode()
            # Add newline for JSON-RPC line protocol
            if not message.endswith(b"\n"):
                message += b"\n"
            self._buffer = message[max_bytes:]
            return message[:max_bytes]
        except Exception as e:
            raise anyio.EndOfStream from e

    async def aclose(self) -> None:
        pass


class _WebSocketWriteStream(ByteSendStream):
    """Adapter to write to WebSocket as a ByteSendStream."""

    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket

    async def send(self, item: bytes) -> None:
        # WebSocket sends complete messages, strip newline if present
        message = item.decode().strip()
        if message:
            await self._websocket.send(message)

    async def aclose(self) -> None:
        pass


def _ensure_factory(
    agent: Agent | Callable[[AgentSideConnection], Agent],
) -> Callable[[AgentSideConnection], Agent]:
    """Ensure agent is wrapped in a factory function."""
    if callable(agent) and not hasattr(agent, "initialize"):
        return agent  # Already a factory

    # Wrap instance in factory
    def factory(connection: AgentSideConnection) -> Agent:
        return agent  # type: ignore[return-value]  # ty:ignore[invalid-explicit-return]

    return factory


# =============================================================================
# Subprocess Transport Utilities (for spawning agents)
# =============================================================================


DEFAULT_INHERITED_ENV_VARS = (
    [
        "APPDATA",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "USERNAME",
        "USERPROFILE",
    ]
    if os.name == "nt"
    else ["HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER"]
)


def default_environment() -> dict[str, str]:
    """Return a trimmed environment based on MCP best practices."""
    env: dict[str, str] = {}
    for key in DEFAULT_INHERITED_ENV_VARS:
        value = os.environ.get(key)
        if value is None:
            continue
        # Skip function-style env vars on some shells (see MCP reference)
        if value.startswith("()"):
            continue
        env[key] = value
    return env


async def _drain_stderr_to_log(
    process: Process,
    command: str,
    log_level: int = logging.WARNING,
) -> None:
    """Read stderr from a process and log each line.

    Args:
        process: The subprocess to read stderr from.
        command: Command name for log messages.
        log_level: Log level for stderr output (default WARNING).
    """
    if process.stderr is None:
        return

    try:
        async for line_bytes in process.stderr:
            line = line_bytes.decode(errors="replace").rstrip()
            if line:
                logger.log(log_level, "[%s stderr] %s", command, line)
    except anyio.EndOfStream:
        pass
    except Exception:
        logger.debug("Error reading stderr from %s", command, exc_info=True)


@asynccontextmanager
async def spawn_stdio_transport(
    command: str,
    *args: str,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    stderr: int | None = subprocess.PIPE,
    shutdown_timeout: float = 2.0,
    log_stderr: bool = False,
    stderr_log_level: int = logging.WARNING,
) -> AsyncGenerator[tuple[ByteReceiveStream, ByteSendStream, Process]]:
    """Launch a subprocess and expose its stdio streams as anyio streams.

    This mirrors the defensive shutdown behaviour used by the MCP Python SDK:
    close stdin first, wait for graceful exit, then escalate to terminate/kill.

    Args:
        command: The command to execute.
        *args: Arguments for the command.
        env: Environment variables (merged with defaults).
        cwd: Working directory for the subprocess.
        stderr: How to handle stderr (default: subprocess.PIPE).
        shutdown_timeout: Timeout for graceful shutdown.
        log_stderr: If True, read stderr in background and log each line.
        stderr_log_level: Log level for stderr output (default WARNING).
    """
    merged_env = default_environment()
    if env:
        merged_env.update(env)

    process = await anyio.open_process(
        [command, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr,
        env=merged_env,
        cwd=str(cwd) if cwd is not None else None,
    )

    if process.stdout is None or process.stdin is None:
        process.kill()
        await process.wait()
        raise RuntimeError("spawn_stdio_transport requires stdout/stdin pipes")

    stderr_task: asyncio.Task[None] | None = None
    if log_stderr and process.stderr is not None:
        stderr_task = asyncio.create_task(_drain_stderr_to_log(process, command, stderr_log_level))

    try:
        yield process.stdout, process.stdin, process
    finally:
        # Cancel stderr logging task
        if stderr_task is not None:
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task

        # Attempt graceful stdin shutdown first. Bounded: closing waits for buffered
        # data to drain, which never happens if the child stopped reading.
        if process.stdin is not None:
            with contextlib.suppress(Exception), anyio.move_on_after(shutdown_timeout):
                await process.stdin.aclose()

        try:
            with anyio.fail_after(shutdown_timeout):
                await process.wait()
        except TimeoutError:
            process.terminate()
            try:
                with anyio.fail_after(shutdown_timeout):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
