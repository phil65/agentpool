"""Tests for StepTracker event translation."""

from google.antigravity.connections.local.types import RunCommandResult
from google.antigravity.types import (
    Step,
    StepSource,
    StepStatus,
    StepTarget,
    StepType,
    ToolCall,
    ToolResult,
)

from agentpool.agents.antigravity_agent.steps import StepTracker
from agentpool.agents.events import (
    CompactionEvent,
    PartDeltaEvent,
    PartStartEvent,
    SubAgentEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)


def test_thinking_and_text_streaming() -> None:
    tracker = StepTracker(agent_name="agy", message_id="m1", session_id="s1")
    steps = [
        Step(
            id="t:1",
            step_index=1,
            source=StepSource.MODEL,
            target=StepTarget.USER,
            status=StepStatus.ACTIVE,
            thinking_delta="Let me ",
        ),
        Step(
            id="t:1",
            step_index=1,
            source=StepSource.MODEL,
            target=StepTarget.USER,
            status=StepStatus.ACTIVE,
            thinking_delta="think.",
        ),
        Step(
            id="t:1",
            step_index=1,
            source=StepSource.MODEL,
            target=StepTarget.USER,
            status=StepStatus.ACTIVE,
            content_delta="Hello ",
        ),
        Step(
            id="t:1",
            step_index=1,
            source=StepSource.MODEL,
            target=StepTarget.USER,
            status=StepStatus.DONE,
            content="Hello world!",
            content_delta="world!",
        ),
    ]
    events = [e for s in steps for e in tracker.process_step(s)]
    assert [type(e) for e in events] == [
        PartStartEvent,
        PartDeltaEvent,
        PartStartEvent,
        PartDeltaEvent,
    ]
    assert events[0].part.content == "Let me "
    assert events[1].delta.content_delta == "think."
    assert events[2].part.content == "Hello "
    assert events[3].delta.content_delta == "world!"


def test_tool_call_with_post_hook_result() -> None:
    tool_results: dict[str, ToolResult] = {}
    tracker = StepTracker(
        agent_name="agy", message_id="m1", session_id="s1", tool_results=tool_results
    )
    call = ToolCall(
        id="t:2",
        step_id="t:2",
        name="run_command",
        args={"CommandLine": "echo hi"},
    )
    start_step = Step(
        id="t:2",
        step_index=2,
        type=StepType.TOOL_CALL,
        source=StepSource.MODEL,
        target=StepTarget.ENVIRONMENT,
        status=StepStatus.ACTIVE,
        tool_calls=[call],
    )
    started = list(tracker.process_step(start_step))
    assert len(started) == 1
    assert isinstance(started[0], ToolCallStartEvent)
    assert started[0].kind == "execute"
    assert started[0].title == "`echo hi`"

    # Simulate PostToolCallHook storing the structured tool output before DONE step
    tool_results["t:2"] = ToolResult(
        id="t:2",
        step_id="t:2",
        name="run_command",
        result=RunCommandResult(output="hi\n"),
    )
    done_step = start_step.model_copy(update={"status": StepStatus.DONE})
    completed = list(tracker.process_step(done_step))
    assert len(completed) == 1
    assert isinstance(completed[0], ToolCallCompleteEvent)
    assert completed[0].tool_result == "hi\n"
    assert completed[0].error is None


def test_unclosed_tool_flushed_at_turn_end() -> None:
    tool_results: dict[str, ToolResult] = {
        "custom-1": ToolResult(id="custom-1", name="echo", result={"echoed": "ok"})
    }
    tracker = StepTracker(
        agent_name="agy", message_id="m1", session_id="s1", tool_results=tool_results
    )
    active_step = Step(
        id="custom-1",
        step_index=1,
        type=StepType.TOOL_CALL,
        source=StepSource.MODEL,
        target=StepTarget.ENVIRONMENT,
        status=StepStatus.ACTIVE,
        tool_calls=[ToolCall(id="custom-1", name="echo", args={"msg": "ok"})],
    )
    assert len(list(tracker.process_step(active_step))) == 1
    flushed = list(tracker.flush_open_tools())
    assert len(flushed) == 1
    assert isinstance(flushed[0], ToolCallCompleteEvent)
    assert flushed[0].tool_result == {"echoed": "ok"}


def test_compaction_finish_and_subagent_events() -> None:
    tracker = StepTracker(agent_name="agy", message_id="m1", session_id="s1")
    compacting = Step(
        id="t:5",
        step_index=5,
        type=StepType.COMPACTION,
        status=StepStatus.ACTIVE,
    )
    compacted = compacting.model_copy(update={"status": StepStatus.DONE})
    events = [*tracker.process_step(compacting), *tracker.process_step(compacted)]
    assert [type(e) for e in events] == [CompactionEvent, CompactionEvent]
    assert events[0].phase == "starting"
    assert events[1].phase == "completed"

    finish_step = Step(
        id="t:6",
        step_index=6,
        type=StepType.FINISH,
        status=StepStatus.DONE,
        structured_output={"answer": 42},
    )
    assert list(tracker.process_step(finish_step)) == []
    assert tracker.structured_output == {"answer": 42}

    sub_step = Step(
        id="sub:1",
        step_index=1,
        trajectory_id="sub-traj",
        parent_trajectory_id="root-traj",
        depth=1,
        source=StepSource.MODEL,
        target=StepTarget.USER,
        status=StepStatus.ACTIVE,
        content_delta="Subagent output",
    )
    sub_events = list(tracker.process_step(sub_step))
    assert len(sub_events) == 1
    assert isinstance(sub_events[0], SubAgentEvent)
    assert sub_events[0].depth == 1
    assert isinstance(sub_events[0].event, PartStartEvent)
