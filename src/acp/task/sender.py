"""Sender class for sending messages to a remote peer."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

import anyenv
import anyio


if TYPE_CHECKING:
    from anyio.abc import ByteSendStream

    from acp.task.supervisor import TaskSupervisor


logger = logging.getLogger(__name__)


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
    """Async message sender that queues and transmits JSON-RPC messages."""

    def __init__(self, writer: ByteSendStream, supervisor: TaskSupervisor) -> None:
        self._writer = writer
        self._queue: asyncio.Queue[_PendingSend | None] = asyncio.Queue()
        self._closed = False
        self._task = supervisor.create(
            self._loop(), name="acp.Sender.loop", on_error=self._on_error
        )

    async def send(self, payload: dict[str, Any]) -> None:
        try:
            data = (anyenv.dump_json(payload) + "\n").encode()
        except TypeError:
            offenders = _find_non_serializable(payload)
            logger.exception(
                "Failed to JSON-serialize message payload.\n"
                "Non-serializable values:\n%s\n"
                "Full payload repr:\n%s",
                "\n".join(
                    f"  {path}: {type(value).__name__} = {value!r:.200s}"
                    for path, value in offenders
                ),
                repr(payload)[:2000],
            )
            raise
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self._queue.put(_PendingSend(data, future))
        await future

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                try:
                    await self._writer.send(item.payload)
                except Exception as exc:
                    if not item.future.done():
                        item.future.set_exception(exc)
                    raise
                else:
                    if not item.future.done():
                        item.future.set_result(None)
        except asyncio.CancelledError:
            return
        except anyio.ClosedResourceError:
            return

    def _on_error(self, task: asyncio.Task[Any], exc: BaseException) -> None:
        logging.exception("Send loop failed", exc_info=exc)
