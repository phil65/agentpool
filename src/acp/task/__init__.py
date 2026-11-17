"""Task package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal


if TYPE_CHECKING:
    import asyncio


RpcTaskKind = Literal["request", "notification"]


@dataclass(slots=True)
class RpcTask:
    """An incoming request or notification waiting to be handled.

    Attributes:
        kind: Whether the message is a request or a notification.
        message: The raw JSON-RPC message.
        done: Resolved once the message has been handled. Set for notifications, so the
            connection can wait for notifications received before a given response.
    """

    kind: RpcTaskKind
    message: dict[str, Any]
    done: asyncio.Future[None] | None = None


from .dispatcher import (
    DefaultMessageDispatcher,
    MessageDispatcher,
    NotificationRunner,
    RequestRunner,
)
from .queue import InMemoryMessageQueue, MessageQueue
from .sender import MessageSender
from .state import InMemoryMessageStateStore, MessageStateStore
from .supervisor import TaskSupervisor
from .debug import DebugEntry, DebuggingMessageStateStore

__all__ = [
    "DebugEntry",
    "DebuggingMessageStateStore",
    "DefaultMessageDispatcher",
    "InMemoryMessageQueue",
    "InMemoryMessageStateStore",
    "MessageDispatcher",
    "MessageQueue",
    "MessageSender",
    "MessageStateStore",
    "NotificationRunner",
    "RequestRunner",
    "RpcTask",
    "RpcTaskKind",
    "TaskSupervisor",
]
