"""Keep the tool lifecycle order in streams merged from two sources.

Agents whose backend calls agentpool tools through the MCP tool bridge get a tool call's
events from two channels: the backend announces the call (``ToolCallStartEvent``, from
its own protocol stream) and the bridge runs the tool, whose progress events go to the
agent's event queue. The bridge's HTTP request can be handled before the backend's
notification, so a progress event could reach the merged stream before the start of its
call. Consumers rely on start before progress (see ``ToolCallStartEvent``): the progress
of a call not started yet is held back until its start, its end or the end of the stream.
"""

from __future__ import annotations

from typing import Any

from agentpool.agents.events.events import (
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)


MAX_KNOWN = 1000
"""Started tool calls remembered (progress of an earlier turn's call isn't held back)."""


class ToolEventOrder:
    """Holds back progress events of tool calls whose start wasn't seen yet."""

    def __init__(self) -> None:
        self._started: dict[str, None] = {}  # Insertion-ordered set, bounded
        self._held: dict[str, list[ToolCallProgressEvent]] = {}

    def _mark_started(self, tool_call_id: str) -> None:
        self._started[tool_call_id] = None
        while len(self._started) > MAX_KNOWN:
            del self._started[next(iter(self._started))]

    def feed(self, event: Any) -> list[Any]:
        """The events to pass on now for ``event`` (none while it is held back)."""
        match event:
            case ToolCallStartEvent(tool_call_id=call_id):
                self._mark_started(call_id)
                return [event, *self._held.pop(call_id, [])]
            case ToolCallProgressEvent(tool_call_id=call_id) if call_id not in self._started:
                self._held.setdefault(call_id, []).append(event)
                return []
            case ToolCallCompleteEvent(tool_call_id=call_id):
                return [*self._held.pop(call_id, []), event]
        return [event]

    def flush(self) -> list[ToolCallProgressEvent]:
        """The events still held back (their call was never announced)."""
        held = [event for events in self._held.values() for event in events]
        self._held.clear()
        return held
