"""Live updates of a T3 thread over the native socket, for a turn's loop.

`/mcp` has to be polled, and it shows an agent message only once complete. With a
native sign-in (`pyt3 login-native`), the agent also subscribes to the thread
(`orchestration.subscribeThread`, see `pyt3client.native`): growing message and
reasoning texts come in as they are written, other changes say when to read the
thread over `/mcp` again, and approval requests can be answered
(`runtime-request.respond`), which `/mcp` cannot do.
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from pyt3client.native import T3NativeClient, TurnItem


logger = get_logger(__name__)

TEXT_TYPES = frozenset({"assistant_message", "reasoning"})
"""Items whose growing text the feed passes on as it comes."""


@dataclass
class FeedUpdate:
    """What happened since the last `ThreadFeed.wait()`."""

    texts: list[TurnItem] = field(default_factory=list)
    """Latest state of message and reasoning items that changed, in order."""
    approvals: list[TurnItem] = field(default_factory=list)
    """Approval requests that wait for an answer."""
    questions: list[TurnItem] = field(default_factory=list)
    """Questions (``user_input_request``) that wait for an answer."""
    changed: bool = False
    """Something else changed (tool items, runs, requests): read the thread again."""


@dataclass
class ThreadFeed:
    """Collects a thread's live events until the turn loop takes them (`wait`)."""

    thread_id: str
    lost: bool = False
    """The subscription ended (socket closed or refused): poll instead."""
    _update: FeedUpdate = field(default_factory=FeedUpdate)
    _signal: asyncio.Event = field(default_factory=asyncio.Event)
    _tool_items: dict[str, str] = field(default_factory=dict)
    """Item id of each tool call by the provider's native id (approvals name the latter)."""

    @classmethod
    @asynccontextmanager
    async def follow(cls, client: T3NativeClient, thread_id: str) -> AsyncGenerator[ThreadFeed]:
        """Subscribe to the thread while the context is open."""
        feed = cls(thread_id)
        task = asyncio.create_task(feed._run(client), name=f"t3-feed-{thread_id}")
        try:
            yield feed
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def tool_call_id(self, approval: TurnItem) -> str:
        """The tool call an approval request is about, or the request's own item."""
        if (ref := approval.native_item_ref) and (item_id := self._tool_items.get(ref.native_id)):
            return item_id
        return approval.item_id

    async def wait(self, timeout: float) -> FeedUpdate | None:
        """The updates since the last call, once there are any; None after `timeout`."""
        with suppress(TimeoutError):
            await asyncio.wait_for(self._signal.wait(), timeout)
        if not self._signal.is_set():
            return None
        self._signal.clear()
        update, self._update = self._update, FeedUpdate()
        return update

    def add(self, item: TurnItem) -> None:
        """Take in an updated item (public for tests; the subscription calls it)."""
        if item.type in TEXT_TYPES:
            texts = [t for t in self._update.texts if t.item_id != item.item_id]
            self._update.texts = [*texts, item]
        elif item.type in ("approval_request", "user_input_request"):
            if item.status == "waiting" and item.request_id:
                waiting = (
                    self._update.approvals
                    if item.type == "approval_request"
                    else self._update.questions
                )
                waiting.append(item)
            self._update.changed = True
        else:
            if item.native_item_ref is not None:
                self._tool_items[item.native_item_ref.native_id] = item.item_id
            self._update.changed = True
        self._signal.set()

    def changed(self) -> None:
        """Note a change other than an item (public for tests)."""
        self._update.changed = True
        self._signal.set()

    async def _run(self, client: T3NativeClient) -> None:
        from pyt3client import T3Error
        from pyt3client.native import ThreadEvent

        try:
            async for value in client.subscribe_thread(self.thread_id):
                if not isinstance(value, ThreadEvent):
                    continue
                if (item := value.event.turn_item()) is not None:
                    self.add(item)
                else:
                    self.changed()
        except T3Error as error:
            logger.warning(
                "T3 live updates ended; polling", thread_id=self.thread_id, error=str(error)
            )
        finally:
            self.lost = True
            self.changed()
