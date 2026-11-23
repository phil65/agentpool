"""Stream utilities for merging async iterators."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator


@asynccontextmanager
async def merge_queue_into_iterator[T, V](  # noqa: PLR0915
    primary_stream: AsyncIterable[T],
    secondary_queue: asyncio.Queue[V],
) -> AsyncGenerator[AsyncIterator[T | V]]:
    """Merge a primary async stream with events from a secondary queue.

    Args:
        primary_stream: The main async iterator (e.g., provider events)
        secondary_queue: Queue containing secondary events (e.g., progress events)

    Yields:
        Async iterator that yields events from both sources in real-time.
        Secondary queue is fully drained before the iterator completes.

    Example:
        ```python
        progress_queue: asyncio.Queue[ProgressEvent] = asyncio.Queue()

        async with merge_queue_into_iterator(provider_stream, progress_queue) as events:
            async for event in events:
                print(f"Got event: {event}")
        ```
    """
    # Create a queue for all merged events
    event_queue: asyncio.Queue[V | T | None] = asyncio.Queue()
    primary_done = asyncio.Event()
    primary_exception: BaseException | None = None
    # Track if we've signaled the end of streams
    end_signaled = False

    # Task to read from primary stream and put into merged queue
    async def primary_task() -> None:
        nonlocal primary_exception, end_signaled
        try:
            async for event in primary_stream:
                await event_queue.put(event)
        except asyncio.CancelledError:
            # Signal completion and unblock merged_events before re-raising
            primary_done.set()
            if not end_signaled:
                end_signaled = True
                await event_queue.put(None)
            raise
        except BaseException as e:  # noqa: BLE001
            primary_exception = e
        finally:
            primary_done.set()

    # Task to read from secondary queue and put into merged queue
    async def secondary_task() -> None:
        nonlocal end_signaled
        try:
            while not primary_done.is_set():
                try:
                    secondary_event = await asyncio.wait_for(secondary_queue.get(), timeout=0.01)
                    await event_queue.put(secondary_event)
                except TimeoutError:
                    continue
            # Drain any remaining events after primary completes
            while not secondary_queue.empty():
                try:
                    secondary_event = secondary_queue.get_nowait()
                    await event_queue.put(secondary_event)
                except asyncio.QueueEmpty:
                    break
            # Now signal end of all events (only if not already signaled)
            if not end_signaled:
                end_signaled = True
                await event_queue.put(None)
        except asyncio.CancelledError:
            # Still need to signal completion on cancel (only if not already signaled)
            if not end_signaled:
                end_signaled = True
                await event_queue.put(None)

    # Start both tasks
    primary_task_obj = asyncio.create_task(primary_task())
    secondary_task_obj = asyncio.create_task(secondary_task())

    try:
        # Create async iterator that drains the merged queue
        async def merged_events() -> AsyncIterator[V | T]:
            while True:
                event = await event_queue.get()
                if event is None:  # End of all streams
                    break
                yield event
            # Re-raise any exception from primary stream after draining
            if primary_exception is not None:
                raise primary_exception

        yield merged_events()

    finally:
        # Clean up tasks - cancel BOTH tasks
        primary_task_obj.cancel()
        secondary_task_obj.cancel()
        await asyncio.gather(primary_task_obj, secondary_task_obj, return_exceptions=True)


@asynccontextmanager
async def closing[T](stream: AsyncIterator[T]) -> AsyncGenerator[AsyncIterator[T]]:
    """Use ``stream``, closing it when the block ends: also on errors and cancellation.

    Like ``contextlib.aclosing``, for streams typed as plain iterators. A stream left
    open (the consumer cancelled mid-iteration) is closed by the garbage collector in
    another task, too late, e.g. for a command that keeps running until it is.
    """
    try:
        yield stream
    finally:
        if (aclose := getattr(stream, "aclose", None)) is not None:
            await aclose()
