"""FastMCP test server publishing a `tick` event every 100 ms (MCP Events, SEP-3415).

Positions are time-based (ticks since the epoch), so a subscriber resuming from a cursor
gets the ticks it missed.
"""

import time

from fastmcp import FastMCP
from fastmcp.experimental.events import Event, EventBatch, EventPoll, EventsExtension


TICK_SECONDS = 0.1

mcp = FastMCP("Events Test Server")
events = EventsExtension()
mcp.add_extension(events)


def _now() -> int:
    return int(time.time() / TICK_SECONDS)


@events.event(
    name="tick",
    description="A tick every 100 ms",
    input_schema={
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
    },
)
def tick(poll: EventPoll) -> EventBatch:
    now = _now()
    if poll.cursor is None:  # Subscribing: nothing yet, current position
        return EventBatch(cursor=str(now), next_poll_ms=100)
    start = int(poll.cursor)
    label = str(poll.arguments.get("label", ""))
    ticks = [Event(data={"n": n, "label": label}, event_id=str(n)) for n in range(start, now)]
    return EventBatch(events=ticks[:5], cursor=str(start + len(ticks[:5])), next_poll_ms=100)


if __name__ == "__main__":
    mcp.run(show_banner=False)
