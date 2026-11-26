"""merge_queue_into_iterator: both sources in order, without polling the queue."""

import asyncio
from typing import TYPE_CHECKING

from agentpool.utils.streams.helpers import merge_queue_into_iterator


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _CountingQueue(asyncio.Queue[str]):
    gets = 0

    async def get(self) -> str:
        self.gets += 1
        return await super().get()


async def test_secondary_events_arrive_without_polling() -> None:
    queue = _CountingQueue()

    async def primary() -> AsyncIterator[str]:
        yield "a"
        await queue.put("progress")
        await asyncio.sleep(0.3)  # A model thinking: nothing happens
        await queue.put("late progress")
        yield "b"

    async with merge_queue_into_iterator(primary(), queue) as events:
        seen = [event async for event in events]
    assert seen[0] == "a"
    assert sorted(seen) == sorted(["a", "progress", "late progress", "b"])
    assert queue.gets <= 4  # One per event (and the one cancelled at the end), not every 10 ms


async def test_primary_error_is_raised_after_the_queue_drained() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def primary() -> AsyncIterator[str]:
        await queue.put("progress")
        yield "a"
        raise ValueError("boom")

    seen: list[str] = []
    try:
        async with merge_queue_into_iterator(primary(), queue) as events:
            async for event in events:
                seen.append(event)  # noqa: PERF401
    except ValueError:
        pass
    else:
        raise AssertionError("not raised")
    assert sorted(seen) == ["a", "progress"]
