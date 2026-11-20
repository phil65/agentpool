"""Event bus feeding the volatile ``GET /api/event`` SSE stream.

Mirrors OpenCode's ``EventFeed``: every subscriber gets a bounded queue of already
encoded SSE frames. Subscribers that fall behind are dropped (the stream is volatile,
durable session history is served from the session log instead).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING

from agentpool.log import get_logger
from agentpool_server.opencode_v2_server.events import dump_model


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from pydantic import BaseModel


logger = get_logger(__name__)

SUBSCRIBER_CAPACITY = 4096
"""Maximum number of pending frames per subscriber before it gets dropped."""


def sse_frame(event: BaseModel) -> str:
    """Encode an event as an SSE ``data:`` frame."""
    return f"data: {json.dumps(dump_model(event), separators=(',', ':'))}\n\n"


class SubscriberOverflowError(Exception):
    """Raised into a subscriber stream that fell too far behind."""


@dataclass
class _Subscriber:
    queue: asyncio.Queue[str | None]
    types: frozenset[str] | None
    overflowed: bool = False


@dataclass
class EventBus:
    """Fan-out of public events to SSE subscribers."""

    capacity: int = SUBSCRIBER_CAPACITY
    _subscribers: list[_Subscriber] = field(default_factory=list)

    @property
    def subscriber_count(self) -> int:
        """Number of connected subscribers."""
        return len(self._subscribers)

    def publish(self, event: BaseModel, event_type: str) -> None:
        """Publish an event to all subscribers interested in its type.

        Args:
            event: The event model
            event_type: The event's ``type`` discriminator
        """
        if not self._subscribers:
            return
        frame: str | None = None
        for subscriber in list(self._subscribers):
            if subscriber.types is not None and event_type not in subscriber.types:
                continue
            frame = frame or sse_frame(event)
            try:
                subscriber.queue.put_nowait(frame)
            except asyncio.QueueFull:
                logger.warning("Dropping slow event subscriber", capacity=self.capacity)
                subscriber.overflowed = True
                self._subscribers.remove(subscriber)
                # Make room for the terminating sentinel so the stream ends promptly.
                while not subscriber.queue.empty():
                    subscriber.queue.get_nowait()
                subscriber.queue.put_nowait(None)

    async def subscribe(
        self, types: frozenset[str] | None = None, *, initial: str | None = None
    ) -> AsyncGenerator[str]:
        """Yield encoded SSE frames until the subscriber disconnects or overflows.

        Args:
            types: Only receive events of these types (None for all)
            initial: Optional frame sent first (e.g. ``server.connected``)
        """
        subscriber = _Subscriber(queue=asyncio.Queue(self.capacity), types=types)
        self._subscribers.append(subscriber)
        try:
            if initial is not None:
                yield initial
            while (frame := await subscriber.queue.get()) is not None:
                yield frame
            if subscriber.overflowed:
                raise SubscriberOverflowError
        finally:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def close(self) -> None:
        """End all subscriber streams."""
        for subscriber in self._subscribers:
            while not subscriber.queue.empty():
                subscriber.queue.get_nowait()
            subscriber.queue.put_nowait(None)
        self._subscribers.clear()
