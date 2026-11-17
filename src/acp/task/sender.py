"""Sender class for sending messages to a remote peer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import anyenv
import structlog


if TYPE_CHECKING:
    from anyio.abc import ByteSendStream

    from acp.task.supervisor import TaskSupervisor


logger = structlog.get_logger(__name__)


_JSON_PRIMITIVE = (str, int, float, bool, type(None))


def _find_non_serializable(obj: Any, path: str = "$") -> list[tuple[str, Any]]:
    """Walk a nested dict/list and return paths to non-JSON-serializable values."""
    results: list[tuple[str, Any]] = []
    match obj:
        case dict():
            for key, value in obj.items():
                results.extend(_find_non_serializable(value, f"{path}.{key}"))
        case list() | tuple():
            for idx, value in enumerate(obj):
                results.extend(_find_non_serializable(value, f"{path}[{idx}]"))
        case _ if not isinstance(obj, _JSON_PRIMITIVE):
            results.append((path, obj))
    return results


@dataclass(slots=True)
class _PendingSend:
    payload: bytes
    future: asyncio.Future[None]


class MessageSender:
    """Serialize JSON-RPC messages as NDJSON frames onto a byte stream.

    Frames are written by a single writer task, in the order they were enqueued. Writing
    outside the caller's task keeps frames intact: cancelling a caller can never leave a
    half-written frame on the stream (anyio byte streams may write in several steps).

    Once the stream fails or the sender is closed, every queued and every later send
    fails with :class:`ConnectionError` instead of waiting forever.
    """

    def __init__(
        self,
        writer: ByteSendStream,
        supervisor: TaskSupervisor,
        *,
        flush_timeout: float = 5.0,
    ) -> None:
        """Start the writer task.

        Args:
            writer: Byte stream the frames are written to.
            supervisor: Supervisor owning the writer task.
            flush_timeout: How long ``close()`` waits for queued frames to be written.
        """
        self._writer = writer
        self._flush_timeout = flush_timeout
        self._queue: asyncio.Queue[_PendingSend | None] = asyncio.Queue()
        self._closed = False
        self._failure: ConnectionError | None = None
        self._task = supervisor.create(self._loop(), name="acp.Sender.loop")

    @property
    def closed(self) -> bool:
        """Whether the sender stopped accepting messages."""
        return self._closed

    def send_nowait(self, payload: dict[str, Any]) -> asyncio.Future[None]:
        """Enqueue a message without waiting for it to be written.

        The message is committed to the stream as soon as this returns, so it is written
        even if the caller is cancelled right after (it is flushed by ``close()`` too).

        Returns:
            A future that completes once the frame was written, or fails with
            ``ConnectionError`` if it could not be.

        Raises:
            ConnectionError: The sender is closed or the stream failed earlier.
            TypeError: The payload is not JSON-serializable.
        """
        if self._closed:
            raise ConnectionError("Sender closed") from self._failure
        data = self._encode(payload)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(_PendingSend(data, future))
        return future

    async def send(self, payload: dict[str, Any]) -> None:
        """Write a message and wait until it is on the stream.

        Raises:
            ConnectionError: The sender is closed or the stream failed.
            TypeError: The payload is not JSON-serializable.
        """
        await self.send_nowait(payload)

    async def close(self) -> None:
        """Stop accepting messages and flush the ones already queued.

        Queued frames get up to ``flush_timeout`` seconds to be written; whatever is
        still pending afterwards fails with ``ConnectionError``.
        """
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(None)
        await asyncio.wait({self._task}, timeout=self._flush_timeout)
        if not self._task.done():
            self._task.cancel()
            await asyncio.wait({self._task})

    def _encode(self, payload: dict[str, Any]) -> bytes:
        try:
            return (anyenv.dump_json(payload) + "\n").encode()
        except TypeError:
            offenders = "\n".join(
                f"  {path}: {type(value).__name__} = {value!r:.200s}"
                for path, value in _find_non_serializable(payload)
            )
            logger.exception(
                "Failed to JSON-serialize message payload",
                non_serializable=offenders,
                payload=repr(payload)[:2000],
            )
            raise

    async def _loop(self) -> None:
        current: _PendingSend | None = None
        try:
            while (current := await self._queue.get()) is not None:
                try:
                    await self._writer.send(current.payload)
                except Exception as exc:
                    error = ConnectionError(f"Failed to write message: {exc!r}")
                    error.__cause__ = exc
                    self._failure = error
                    logger.debug("Writing to the stream failed", exc_info=exc)
                    return
                if not current.future.done():
                    current.future.set_result(None)
        finally:
            # Reached on stream failure, on close() and when the writer task is cancelled.
            self._closed = True
            error = self._failure or ConnectionError("Sender closed")
            if current is not None and not current.future.done():
                current.future.set_exception(error)
            while not self._queue.empty():
                item = self._queue.get_nowait()
                if item is not None and not item.future.done():
                    item.future.set_exception(error)
