"""Tool calls run through the MCP bridge: progress never before the call's start."""

from agentpool.agents.events import (
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.agents.events.ordering import ToolEventOrder


def _start(call_id: str) -> ToolCallStartEvent:
    return ToolCallStartEvent(tool_call_id=call_id, tool_name="bash", title="Running")


def _progress(call_id: str) -> ToolCallProgressEvent:
    return ToolCallProgressEvent(tool_call_id=call_id, title="out")


def _complete(call_id: str) -> ToolCallCompleteEvent:
    return ToolCallCompleteEvent(
        tool_name="bash",
        tool_call_id=call_id,
        tool_input={},
        tool_result="ok",
        agent_name="a",
        message_id="",
    )


def _run(order: ToolEventOrder, events: list[object]) -> list[object]:
    out = [ready for event in events for ready in order.feed(event)]
    return [*out, *order.flush()]


def test_bridge_progress_before_the_remote_start_is_held_until_the_start() -> None:
    start, progress, complete = _start("c1"), _progress("c1"), _complete("c1")
    assert _run(ToolEventOrder(), [progress, start, complete]) == [start, progress, complete]


def test_progress_of_a_call_never_announced_comes_before_its_end_or_at_the_stream_end() -> None:
    progress, complete = _progress("c1"), _complete("c1")
    assert _run(ToolEventOrder(), [progress, complete]) == [progress, complete]
    other = _progress("c2")
    assert _run(ToolEventOrder(), [other, "text"]) == ["text", other]


def test_progress_of_an_earlier_turns_call_passes_at_once() -> None:
    order = ToolEventOrder()
    _run(order, [_start("c1"), _complete("c1")])
    late = _progress("c1")  # E.g. a background process still reporting
    assert order.feed(late) == [late]
