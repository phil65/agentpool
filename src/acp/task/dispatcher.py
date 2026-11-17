"""Message dispatcher for ACP tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from acp.exceptions import RequestError
from acp.task.queue import InMemoryMessageQueue
from acp.task.state import InMemoryMessageStateStore


if TYPE_CHECKING:
    from acp.task import RpcTask
    from acp.task.queue import MessageQueue
    from acp.task.state import MessageStateStore
    from acp.task.supervisor import TaskSupervisor


RequestRunner = Callable[["RpcTask"], Awaitable[dict[str, Any]]]
"""Handles a request and returns the JSON-RPC response payload that was sent."""
NotificationRunner = Callable[["RpcTask"], Awaitable[None]]


class MessageDispatcher(Protocol):
    """Protocol for message dispatchers."""

    def __init__(
        self,
        *,
        queue: MessageQueue,
        supervisor: TaskSupervisor,
        store: MessageStateStore,
        request_runner: RequestRunner,
        notification_runner: NotificationRunner,
    ) -> None: ...

    def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass(kw_only=True)
class DefaultMessageDispatcher(MessageDispatcher):
    """Background worker that consumes RPC tasks from a broker.

    Every message is handled in its own task, so a slow handler never blocks the
    others; tasks are started in the order the messages were received.
    """

    supervisor: TaskSupervisor
    request_runner: RequestRunner
    notification_runner: NotificationRunner
    queue: MessageQueue = field(default_factory=InMemoryMessageQueue)
    store: MessageStateStore = field(default_factory=InMemoryMessageStateStore)

    def __post_init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("dispatcher already started")
        self._task = self.supervisor.create(self._run(), name="acp.Dispatcher.loop")

    async def _run(self) -> None:
        try:
            async for task in self.queue:
                try:
                    if self.supervisor.closed:
                        continue  # Connection is shutting down, drain without handling
                    if task.kind == "request":
                        self._dispatch_request(task)
                    else:
                        self._dispatch_notification(task)
                finally:
                    self.queue.task_done()
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        await self.queue.close()
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _dispatch_request(self, task: RpcTask) -> None:
        msg = task.message
        record = self.store.begin_incoming(msg.get("method", ""), msg.get("params"))

        async def runner() -> None:
            response = await self.request_runner(task)
            if "error" in response:
                self.store.fail_incoming(record, RequestError.from_error_obj(response["error"]))
            else:
                self.store.complete_incoming(record, response.get("result"))

        self.supervisor.create(runner(), name="acp.Dispatcher.request")

    def _dispatch_notification(self, task: RpcTask) -> None:
        async def runner() -> None:
            await self.notification_runner(task)

        self.supervisor.create(runner(), name="acp.Dispatcher.notification")
