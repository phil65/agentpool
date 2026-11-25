"""Compaction steps with zero counts and tiny lengths (issue #59.3)."""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
import pytest

from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.messaging.compaction import (
    CompactionPipeline,
    KeepFirstAndLast,
    KeepLastMessages,
    TruncateTextParts,
    compact_conversation,
)


def _messages(n: int = 6) -> list[ModelRequest | ModelResponse]:
    return [
        ModelRequest(parts=[UserPromptPart(content=f"q{i}")])
        if i % 2 == 0
        else ModelResponse(parts=[TextPart(content=f"a{i}")])
        for i in range(n)
    ]


@pytest.mark.parametrize("count_pairs", [True, False])
async def test_keep_last_zero_keeps_nothing(count_pairs: bool) -> None:
    assert await KeepLastMessages(count=0, count_pairs=count_pairs).apply(_messages()) == []


async def test_keep_first_and_last_with_zero_last_does_not_duplicate() -> None:
    msgs = _messages()
    assert await KeepFirstAndLast(first_count=1, last_count=0).apply(msgs) == msgs[:1]


async def test_truncate_shorter_than_its_suffix_stays_within_max_length() -> None:
    step = TruncateTextParts(max_length=2, suffix="...[cut]")
    [response] = await step.apply([ModelResponse(parts=[TextPart(content="a" * 26)])])
    assert isinstance(response, ModelResponse)
    part = response.parts[0]
    assert isinstance(part, TextPart)
    assert len(part.content) <= len("...[cut]")


async def test_compacted_messages_keep_the_session_id() -> None:
    history = MessageHistory()
    msgs = _messages(4)
    history.set_history([
        ChatMessage(content="x", role="assistant", session_id="s1", messages=msgs[:2]),
        ChatMessage(content="y", role="assistant", session_id="s1", messages=msgs[2:]),
    ])
    pipeline = CompactionPipeline(steps=[KeepLastMessages(count=1)])
    await compact_conversation(pipeline, history)
    assert [m.session_id for m in history.get_history()] == ["s1"]
