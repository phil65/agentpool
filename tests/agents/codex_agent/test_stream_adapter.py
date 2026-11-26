"""Tests for the Codex stream adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codexed.models import (
    ItemAgentMessageDeltaNotification,
    ItemCompletedEvent,
    ItemReasoningTextDeltaNotification,
    ItemStartedEvent,
    ThreadItemContextCompaction,
)
from codexed.models.event_data import ItemCompletedData, ItemStartedData
from codexed.models.v2_protocol import AgentMessageDeltaNotification, ReasoningTextDeltaNotification
from pydantic_ai import PartDeltaEvent, PartEndEvent, PartStartEvent, TextPart, ThinkingPart

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


def _text(delta: str) -> ItemAgentMessageDeltaNotification:
    data = AgentMessageDeltaNotification(delta=delta, item_id="m1", thread_id="t", turn_id="u")
    return ItemAgentMessageDeltaNotification(params=data)


def _thinking(delta: str) -> ItemReasoningTextDeltaNotification:
    data = ReasoningTextDeltaNotification(
        content_index=0, delta=delta, item_id="r1", thread_id="t", turn_id="u"
    )
    return ItemReasoningTextDeltaNotification(params=data)


async def test_text_and_thinking_parts_have_start_and_end() -> None:
    """Deltas are framed by PartStart/PartEnd events, as AG-UI needs (#28)."""
    events = await _stream(_thinking("hmm"), _text("P"), _text("ONG"))
    kinds = [(type(e).__name__, e.index) for e in events]
    assert kinds == [
        ("PartStartEvent", 0),
        ("PartDeltaEvent", 0),
        ("PartEndEvent", 0),
        ("PartStartEvent", 1),
        ("PartDeltaEvent", 1),
        ("PartDeltaEvent", 1),
        ("PartEndEvent", 1),
    ]
    assert isinstance(events[0], PartStartEvent)
    assert isinstance(events[0].part, ThinkingPart)
    assert isinstance(events[2], PartEndEvent)
    assert isinstance(events[2].part, ThinkingPart)
    assert isinstance(events[3], PartStartEvent)
    assert isinstance(events[3].part, TextPart)
    text = [e.delta.content_delta for e in events if isinstance(e, PartDeltaEvent) and e.index == 1]
    assert text == ["P", "ONG"]
    assert isinstance(events[-1], PartEndEvent)
    assert isinstance(events[-1].part, TextPart)
