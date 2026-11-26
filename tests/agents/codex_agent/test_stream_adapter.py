"""Tests for the Codex stream adapter."""

from typing import TYPE_CHECKING, Any

from codexed.models import (
    ItemAgentMessageDeltaNotification,
    ItemCompletedEvent,
    ItemReasoningTextDeltaNotification,
    ItemStartedEvent,
    ThreadItemContextCompaction,
    ThreadTokenUsageUpdatedEvent,
)
from codexed.models.event_data import ItemCompletedData, ItemStartedData
from codexed.models.v2_protocol import (
    AgentMessageDeltaNotification,
    ReasoningTextDeltaNotification,
    ThreadTokenUsage,
    ThreadTokenUsageUpdatedNotification,
    TokenUsageBreakdown,
)
from pydantic_ai import PartDeltaEvent, PartEndEvent, PartStartEvent, TextPart, ThinkingPart

from agentpool.agents.codex_agent.stream_adapter import CodexStreamedResponse
from agentpool.agents.events import CompactionEvent, StepCompletedEvent


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


def _usage(last_input: int, last_output: int, total: int) -> ThreadTokenUsageUpdatedEvent:
    def breakdown(input_tokens: int, output_tokens: int) -> TokenUsageBreakdown:
        return TokenUsageBreakdown(
            cached_input_tokens=0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_output_tokens=0,
            total_tokens=input_tokens + output_tokens,
        )

    usage = ThreadTokenUsage(last=breakdown(last_input, last_output), total=breakdown(total, 0))
    data = ThreadTokenUsageUpdatedNotification(thread_id="t", turn_id="u", token_usage=usage)
    return ThreadTokenUsageUpdatedEvent(params=data)


async def test_each_model_request_is_a_completed_step() -> None:
    """Codex reports each request's usage (``last``); a repeated update is no new step."""
    events = await _stream(_usage(100, 10, 110), _usage(100, 10, 110), _usage(150, 20, 280))
    steps = [e for e in events if isinstance(e, StepCompletedEvent)]
    assert [(s.usage.input_tokens, s.usage.output_tokens) for s in steps] == [
        (100, 10),
        (150, 20),
    ]
    assert len({s.step_id for s in steps}) == 2
