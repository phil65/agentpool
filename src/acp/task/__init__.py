"""Task package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


RpcTaskKind = Literal["request", "notification"]


@dataclass(slots=True)
class RpcTask:
    """RpcTask represents a task to be executed by the agent."""

    kind: RpcTaskKind
    message: dict[str, Any]


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
