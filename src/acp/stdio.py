from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import io
import sys
from typing import TYPE_CHECKING, Any

import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream

from acp.agent.connection import AgentSideConnection
from acp.client.connection import ClientSideConnection
from acp.connection import Connection
from acp.transports import spawn_stdio_transport


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Mapping
    from pathlib import Path

    from anyio.abc import Process

    from acp.agent.protocol import Agent
    from acp.client.protocol import Client
    from acp.connection import MethodHandler, StreamObserver


class StdinStream(ByteReceiveStream):
    """Wrapper for stdin that implements ByteReceiveStream interface."""

    async def receive(self, max_bytes: int = 65536) -> bytes:
        """Read bytes from stdin.

        Uses read1() which returns as soon as any data is available,
        rather than blocking until max_bytes are read.
        """
        loop = asyncio.get_running_loop()
        # read1() returns immediately when any data is available (up to max_bytes)
        # unlike read() which blocks until exactly max_bytes are read or EOF
        stdin_buffer = sys.stdin.buffer
        if not isinstance(stdin_buffer, io.BufferedIOBase):
            raise TypeError(f"Unsupported stdin buffer type: {type(stdin_buffer)}")
        data = await loop.run_in_executor(None, lambda: stdin_buffer.read1(max_bytes))
        if not data:
            raise anyio.EndOfStream
        return data

    async def aclose(self) -> None:
        """Close is a no-op for stdin."""


class StdoutStream(ByteSendStream):
    """Wrapper for stdout that implements ByteSendStream interface."""

    async def send(self, item: bytes) -> None:
        """Write bytes to stdout."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write, item)

    def _write(self, data: bytes) -> None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    async def aclose(self) -> None:
        """Close is a no-op for stdout."""


async def stdio_streams() -> tuple[StdinStream, StdoutStream]:
    """Create stdio anyio-compatible streams (cross-platform).

    Returns (stdin, stdout) as byte stream wrappers compatible with
    ByteReceiveStream and ByteSendStream interfaces.
    """
    return StdinStream(), StdoutStream()


@asynccontextmanager
async def spawn_stdio_connection(
    handler: MethodHandler,
    command: str,
    *args: str,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    observers: list[StreamObserver] | None = None,
    log_stderr: bool = False,
) -> AsyncGenerator[tuple[Connection, Process]]:
    """Spawn a subprocess and bind its stdio to a low-level Connection.

    Args:
        handler: Method handler for the connection.
        command: The command to execute.
        *args: Arguments for the command.
        env: Environment variables for the subprocess.
        cwd: Working directory for the subprocess.
        observers: Optional stream observers.
        log_stderr: If True, log stderr output from the subprocess.
    """
    async with spawn_stdio_transport(command, *args, env=env, cwd=cwd, log_stderr=log_stderr) as (
        reader,
        writer,
        process,
    ):
        conn = Connection(handler, writer, reader, observers=observers)
        try:
            yield conn, process
        finally:
            await conn.close()


@asynccontextmanager
async def spawn_agent_process(
    to_client: Callable[[Agent], Client],
    command: str,
    *args: str,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    log_stderr: bool = False,
    **connection_kwargs: Any,
) -> AsyncGenerator[tuple[ClientSideConnection, Process]]:
    """Spawn an ACP agent subprocess and return a ClientSideConnection to it.

    Args:
        to_client: Factory function that creates a Client from an Agent.
        command: The command to execute.
        *args: Arguments for the command.
        env: Environment variables for the subprocess.
        cwd: Working directory for the subprocess.
        log_stderr: If True, log stderr output from the subprocess.
        **connection_kwargs: Additional arguments for ClientSideConnection.
    """
    async with spawn_stdio_transport(
        command,
        *args,
        env=env,
        cwd=cwd,
        log_stderr=log_stderr,
    ) as (reader, writer, process):
        conn = ClientSideConnection(to_client, writer, reader, **connection_kwargs)
        try:
            yield conn, process
        finally:
            await conn.close()


@asynccontextmanager
async def spawn_client_process(
    to_agent: Callable[[AgentSideConnection], Agent],
    command: str,
    *args: str,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    log_stderr: bool = False,
    **connection_kwargs: Any,
) -> AsyncGenerator[tuple[AgentSideConnection, Process]]:
    """Spawn an ACP client subprocess and return an AgentSideConnection to it.

    Args:
        to_agent: Factory function that creates an Agent from an AgentSideConnection.
        command: The command to execute.
        *args: Arguments for the command.
        env: Environment variables for the subprocess.
        cwd: Working directory for the subprocess.
        log_stderr: If True, log stderr output from the subprocess.
        **connection_kwargs: Additional arguments for AgentSideConnection.
    """
    async with spawn_stdio_transport(
        command,
        *args,
        env=env,
        cwd=cwd,
        log_stderr=log_stderr,
    ) as (reader, writer, process):
        conn = AgentSideConnection(to_agent, writer, reader, **connection_kwargs)
        try:
            yield conn, process
        finally:
            await conn.close()


async def run_agent(
    agent: Agent | Callable[[AgentSideConnection], Agent],
    input_stream: ByteSendStream | None = None,
    output_stream: ByteReceiveStream | None = None,
    **connection_kwargs: Any,
) -> None:
    """Run an ACP agent over stdio or provided streams.

    This is the recommended entry point for running agents. It handles stream
    setup and connection lifecycle automatically.

    Args:
        agent: An Agent implementation or a factory callable that takes
            an AgentSideConnection and returns an Agent. Using a factory allows
            the agent to access the connection for client communication.
        input_stream: Optional ByteSendStream for output (defaults to stdio).
        output_stream: Optional ByteReceiveStream for input (defaults to stdio).
        **connection_kwargs: Additional keyword arguments for AgentSideConnection.

    Example with direct agent:
        ```python
        class MyAgent(Agent):
            async def initialize(self, params: InitializeRequest) -> InitializeResponse:
                return InitializeResponse(protocol_version=params.protocol_version)
            # ... implement protocol methods ...

        await run_agent(MyAgent())
        ```

    Example with factory:
        ```python
        class MyAgent(Agent):
            def __init__(self, connection: AgentSideConnection):
                self.connection = connection
            # ... implement protocol methods ...

        def create_agent(conn: AgentSideConnection) -> MyAgent:
            return MyAgent(conn)

        await run_agent(create_agent)
        ```
    """
    if input_stream is None or output_stream is None:
        # For stdio, we need to create byte streams from the text wrappers
        # This follows the MCP SDK server pattern
        _stdin_file, _stdout_file = await stdio_streams()
        # Note: run_agent expects ByteSendStream/ByteReceiveStream but stdio_streams
        # returns text AsyncFiles. For now, we'll use the underlying buffer.
        # TODO: Consider refactoring to use memory object streams like MCP SDK
        msg = "run_agent with stdio streams requires explicit stream arguments for now"
        raise NotImplementedError(msg)

    # Wrap agent instance in factory if needed
    if callable(agent):
        agent_factory = agent
    else:

        def agent_factory(connection: AgentSideConnection) -> Agent:
            return agent

    conn = AgentSideConnection(agent_factory, input_stream, output_stream, **connection_kwargs)
    shutdown_event = asyncio.Event()
    try:
        # Keep the connection alive until cancelled
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await conn.close()


def connect_to_agent(
    client: Client,
    input_stream: ByteSendStream,
    output_stream: ByteReceiveStream,
    **connection_kwargs: Any,
) -> ClientSideConnection:
    """Create a ClientSideConnection to an ACP agent.

    This is the recommended entry point for client-side connections.

    Args:
        client: The client implementation.
        input_stream: ByteSendStream for sending to the agent.
        output_stream: ByteReceiveStream for receiving from the agent.
        **connection_kwargs: Additional keyword arguments for ClientSideConnection.

    Returns:
        A ClientSideConnection connected to the agent.
    """

    # Create a factory that ignores the connection parameter
    def client_factory(connection: Agent) -> Client:
        return client

    return ClientSideConnection(client_factory, input_stream, output_stream, **connection_kwargs)
