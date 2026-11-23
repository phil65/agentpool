"""Utilities for subprocess management with async support."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio

from agentpool import log


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Coroutine
    from typing import Any

    from anyio.abc import ByteReceiveStream, Process


logger = log.get_logger(__name__)


@dataclass
class SubprocessError(RuntimeError):
    """Error raised when a subprocess exits unexpectedly."""

    returncode: int | None
    stderr: str

    def __str__(self) -> str:
        msg = f"Subprocess exited unexpectedly (code {self.returncode})"
        if self.stderr:
            msg = f"{msg}:\n{self.stderr}"
        return msg


async def read_stream(
    stream: ByteReceiveStream | None,
    *,
    timeout: float = 0.5,
    max_bytes: int = 65536,
) -> str:
    """Read all available data from an anyio byte stream.

    Args:
        stream: The anyio ByteReceiveStream to read from
        timeout: Timeout for each read operation
        max_bytes: Maximum bytes to read total

    Returns:
        Decoded string content from the stream
    """
    if stream is None:
        return ""

    chunks: list[bytes] = []
    total_bytes = 0

    try:
        while total_bytes < max_bytes:
            with anyio.move_on_after(timeout) as scope:
                chunk = await stream.receive(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                total_bytes += len(chunk)
            if scope.cancelled_caught:
                break
    except anyio.EndOfStream:
        pass
    except Exception as e:  # noqa: BLE001
        return f"(failed to read stream: {e})"

    return b"".join(chunks).decode(errors="replace").strip()


@contextlib.asynccontextmanager
async def monitor_process(
    process: Process,
    *,
    context: str = "operation",
) -> AsyncGenerator[None]:
    """Context manager that monitors a subprocess for unexpected exit.

    Races the wrapped code against process termination. If the process
    exits before the code completes, raises SubprocessError with stderr.

    Args:
        process: The anyio Process to monitor
        context: Description of what's being done (for error messages)

    Raises:
        SubprocessError: If process exits during the wrapped operation

    Example:
        ```python
        async with monitor_process(process, context="initialization"):
            await do_initialization()
            await create_session()
        ```
    """
    process_wait_task = asyncio.create_task(process.wait())

    try:
        yield
    except BaseException:
        # If the wrapped code raises, cancel the wait task and re-raise
        process_wait_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await process_wait_task
        raise

    # Check if process died during operation
    if process_wait_task.done():
        stderr_output = await read_stream(process.stderr)
        raise SubprocessError(returncode=process.returncode, stderr=stderr_output)

    # Operation completed successfully, cancel the wait task
    process_wait_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await process_wait_task


async def run_with_process_monitor[T](
    process: Process,
    coro: Callable[[], Coroutine[Any, Any, T]],
    *,
    context: str = "operation",
) -> T:
    """Run a coroutine while monitoring a subprocess for unexpected exit.

    Races the coroutine against process termination. If the process
    exits before the coroutine completes, raises SubprocessError with stderr.

    Args:
        process: The anyio Process to monitor
        coro: Async callable to execute
        context: Description of what's being done (for error messages)

    Returns:
        The result of the coroutine

    Raises:
        SubprocessError: If process exits during execution

    Example:
        ```python
        result = await run_with_process_monitor(
            process,
            lambda: initialize_connection(),
            context="initialization",
        )
        ```
    """
    process_wait_task: asyncio.Task[int | None] = asyncio.create_task(process.wait())
    operation_task: asyncio.Task[T] = asyncio.create_task(coro())

    done, pending = await asyncio.wait(
        [process_wait_task, operation_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel pending tasks
    for task in pending:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # Check which completed first
    if process_wait_task in done:
        # Process exited before operation completed
        stderr_output = await read_stream(process.stderr)
        raise SubprocessError(returncode=process.returncode, stderr=stderr_output)
    # Operation completed successfully
    return operation_task.result()


async def start_process(
    startup_command: str,
    startup_delay: float = 0.0,
    *,
    ready_address: tuple[str, int] | None = None,
    startup_timeout: float = 30.0,
) -> asyncio.subprocess.Process:
    """Start a long-running process (a server) and wait until it is up.

    Args:
        startup_command: Shell command to run.
        startup_delay: Extra seconds to wait before checking readiness.
        ready_address: ``(host, port)`` the process listens on. If given, wait until it
            accepts TCP connections instead of trusting a fixed delay.
        startup_timeout: Seconds to wait for ``ready_address`` before giving up.

    The process's stdout is discarded and its stderr goes to ours: a pipe nobody reads
    would fill up and block a chatty server.

    Raises:
        RuntimeError: The process exited during startup.
        TimeoutError: ``ready_address`` did not accept connections in time.
    """
    logger.info("Starting process", command=startup_command)
    process = await asyncio.create_subprocess_shell(
        startup_command,
        stdout=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    if startup_delay:
        await anyio.sleep(startup_delay)
    if ready_address is not None:
        await _wait_until_listening(process, ready_address, startup_timeout)
    if process.returncode is not None:
        msg = f"Startup process exited with code {process.returncode} (see its stderr)"
        raise RuntimeError(msg)
    logger.info("Process started", command=startup_command, pid=process.pid)
    return process


async def _wait_until_listening(
    process: asyncio.subprocess.Process, address: tuple[str, int], timeout: float
) -> None:
    """Poll until ``address`` accepts a TCP connection, the process exits or time runs out."""
    host, port = address
    deadline = anyio.current_time() + timeout
    while process.returncode is None:
        try:
            stream = await anyio.connect_tcp(host, port)
        except OSError:
            if anyio.current_time() >= deadline:
                msg = f"Server did not listen on {host}:{port} within {timeout:g} s"
                raise TimeoutError(msg) from None
            await anyio.sleep(0.1)
        else:
            await stream.aclose()
            return
