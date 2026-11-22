"""T3 thread items as agentpool stream events."""

from __future__ import annotations

from typing import Any

from pydantic_ai import PartDeltaEvent, PartStartEvent, TextPart, ThinkingPart
from pydantic_ai.messages import ThinkingPartDelta
from pyt3client.models.tools import Item

from agentpool.agents.events import (
    NoticeEvent,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.agents.t3_agent.items import ItemTracker


def _item(item_id: str, type_: str, status: str, text: str = "", title: str = "") -> Item:
    return Item.model_validate({
        "position": 0,
        "visibility": "local",
        "sourceThreadId": "th",
        "itemId": item_id,
        "runId": "run",
        "messageId": None,
        "createdBy": "agent",
        "creationSource": "provider",
        "type": type_,
        "status": status,
        "title": title or None,
        "text": text,
        "textTruncated": False,
        "updatedAt": "2026-11-22T00:00:00Z",
    })


def _events(tracker: ItemTracker, *items: Item) -> list[Any]:
    return list(tracker.update(items))


def test_growing_reasoning_streams_as_thinking_deltas() -> None:
    tracker = ItemTracker(agent_name="t3", message_id="m")
    [start] = _events(tracker, _item("r", "reasoning", "running", "Let me"))
    [delta] = _events(tracker, _item("r", "reasoning", "running", "Let me think"))
    assert _events(tracker, _item("r", "reasoning", "completed", "Let me think")) == []
    assert start == PartStartEvent(index=0, part=ThinkingPart(content="Let me"))
    assert isinstance(delta, PartDeltaEvent)
    assert delta.delta == ThinkingPartDelta(content_delta=" think")


def test_assistant_message_that_appears_complete() -> None:
    tracker = ItemTracker(agent_name="t3", message_id="m")
    _events(tracker, _item("r", "reasoning", "completed", "hmm"))
    [start] = _events(tracker, _item("a", "assistant_message", "completed", "Hello"))
    assert start == PartStartEvent(index=1, part=TextPart(content="Hello"))
    assert tracker.reply == "Hello"
    assert not tracker.pending


def test_command_runs_as_a_tool_call() -> None:
    tracker = ItemTracker(agent_name="t3", message_id="m")
    [start] = _events(tracker, _item("c", "command_execution", "running", "$ echo hi"))
    [progress] = _events(tracker, _item("c", "command_execution", "running", "$ echo hi\nh"))
    assert tracker.pending
    [done] = _events(tracker, _item("c", "command_execution", "completed", "$ echo hi\nhi"))
    assert isinstance(start, ToolCallStartEvent)
    assert (start.title, start.kind) == ("`echo hi`", "execute")
    assert start.raw_input == {"command": "echo hi"}
    assert isinstance(progress, ToolCallProgressEvent)
    assert isinstance(done, ToolCallCompleteEvent)
    assert (done.tool_result, done.error) == ("$ echo hi\nhi", None)
    assert not tracker.pending
    assert _events(tracker, _item("c", "command_execution", "completed", "$ echo hi\nhi")) == []


def test_tool_seen_only_when_finished_and_failures() -> None:
    tracker = ItemTracker(agent_name="t3", message_id="m")
    events = _events(tracker, _item("d", "dynamic_tool", "failed", "boom", title="search"))
    assert [type(e) for e in events] == [ToolCallStartEvent, ToolCallCompleteEvent]
    assert events[0].title == "search"
    assert events[1].error == "dynamic_tool failed"


def test_notices_once_and_ignored_items() -> None:
    tracker = ItemTracker(agent_name="t3", message_id="m")
    notice = _item("n", "system_notice", "completed", "Rate limit close")
    [event] = _events(tracker, notice, _item("u", "user_message", "completed", "hi"))
    assert event == NoticeEvent(title="Rate limit close", severity="info")
    assert _events(tracker, notice, _item("k", "checkpoint", "completed", "[]")) == []
