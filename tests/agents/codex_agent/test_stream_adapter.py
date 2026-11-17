"""Tests for the Codex stream adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codexed.models import ItemCompletedEvent, ItemStartedEvent, ThreadItemContextCompaction
from codexed.models.event_data import ItemCompletedData, ItemStartedData

from agentpool.agents.codex_agent.stream_adapter import CodexStreamedResponse
from agentpool.agents.events import CompactionEvent


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _stream(*events: Any) -> list[Any]:
    async def source() -> AsyncIterator[Any]:
        for event in events:
            yield event

    response = CodexStreamedResponse(provider_name="codex", stream=source())
    return [event async for event in response._get_event_iterator()]


async def test_context_compaction_item_lifecycle() -> None:
    item = ThreadItemContextCompaction(id="c1")
    started = ItemStartedData(thread_id="t", turn_id="u", item=item, started_at_ms=1)
    completed = ItemCompletedData(thread_id="t", turn_id="u", item=item, completed_at_ms=2)
    events = await _stream(
        ItemStartedEvent(params=started),
        ItemCompletedEvent(params=completed),
    )
    assert events == [
        CompactionEvent(session_id="t", phase="starting"),
        CompactionEvent(session_id="t", phase="completed"),
    ]
