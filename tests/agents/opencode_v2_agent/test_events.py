"""Tests for V2EventTracker SSE event translation."""

from __future__ import annotations

from decimal import Decimal
from itertools import count

from agentpool.agents.events import (
    CompactionEvent,
    NoticeEvent,
    PartDeltaEvent,
    PartStartEvent,
    RetryEvent,
    RunErrorEvent,
    StepCompletedEvent,
    StepStartedEvent,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.agents.opencode_v2_agent.events import V2EventTracker
from agentpool_server.opencode_v2_server.events import build_event
from opencode_v2_sdk.models import (
    ModelRef,
    SessionCompactionEnded,
    SessionCompactionEndedData,
    SessionCompactionStarted,
    SessionCompactionStartedData,
    SessionExecutionFailed,
    SessionExecutionFailedData,
    SessionExecutionSucceeded,
    SessionExecutionSucceededData,
    SessionReasoningDelta,
    SessionReasoningDeltaData,
    SessionRetryScheduled,
    SessionRetryScheduledData,
    SessionStepEnded,
    SessionStepEndedData,
    SessionStepStarted,
    SessionStepStartedData,
    SessionStructuredError,
    SessionTextDelta,
    SessionTextDeltaData,
    SessionToolCalled,
    SessionToolCalledData,
    SessionToolFailed,
    SessionToolFailedData,
    SessionToolInputStarted,
    SessionToolInputStartedData,
    SessionToolProgress,
    SessionToolProgressData,
    SessionToolSuccess,
    SessionToolSuccessData,
    TokenUsageInfo,
    TokenUsageInfoCache,
    ToolTextContent,
    TuiToastShow,
    TuiToastShowData,
)


_seq = count(1)


def _ev(cls, data):
    return build_event(cls, data, aggregate_id="ses_1", seq=next(_seq))


def test_full_turn_event_translation() -> None:
    tracker = V2EventTracker(session_id="ses_1", agent_name="oc", message_id="m1")
    v2_events = [
        _ev(
            SessionStepStarted,
            SessionStepStartedData(
                session_id="ses_1",
                assistant_message_id="msg_1",
                agent="build",
                model=ModelRef(id="gpt-5", provider_id="openai"),
                started=1000,
            ),
        ),
        _ev(
            SessionReasoningDelta,
            SessionReasoningDeltaData(
                session_id="ses_1", assistant_message_id="msg_1", ordinal=0, delta="Thinking..."
            ),
        ),
        _ev(
            SessionToolInputStarted,
            SessionToolInputStartedData(
                session_id="ses_1", assistant_message_id="msg_1", id="call_1", name="bash"
            ),
        ),
        _ev(
            SessionToolCalled,
            SessionToolCalledData(
                session_id="ses_1",
                assistant_message_id="msg_1",
                id="call_1",
                input={"command": "echo hi"},
                executed=False,
            ),
        ),
        _ev(
            SessionToolProgress,
            SessionToolProgressData(
                session_id="ses_1",
                assistant_message_id="msg_1",
                id="call_1",
                metadata={"title": "Running echo", "output": "hi"},
            ),
        ),
        _ev(
            SessionToolSuccess,
            SessionToolSuccessData(
                session_id="ses_1",
                assistant_message_id="msg_1",
                id="call_1",
                content=[ToolTextContent(text="hi\n")],
                executed=True,
            ),
        ),
        _ev(
            SessionTextDelta,
            SessionTextDeltaData(
                session_id="ses_1", assistant_message_id="msg_1", ordinal=0, delta="Hello "
            ),
        ),
        _ev(
            SessionTextDelta,
            SessionTextDeltaData(
                session_id="ses_1", assistant_message_id="msg_1", ordinal=0, delta="world"
            ),
        ),
        _ev(
            SessionStepEnded,
            SessionStepEndedData(
                session_id="ses_1",
                assistant_message_id="msg_1",
                finish="stop",
                cost=0.0025,
                tokens=TokenUsageInfo(
                    input=20,
                    output=10,
                    reasoning=5,
                    cache=TokenUsageInfoCache(read=4, write=0),
                ),
            ),
        ),
        _ev(SessionExecutionSucceeded, SessionExecutionSucceededData(session_id="ses_1")),
    ]

    emitted = [native for ev in v2_events for native in tracker.handle(ev)]
    assert [type(e) for e in emitted] == [
        StepStartedEvent,
        PartStartEvent,
        ToolCallStartEvent,
        ToolCallProgressEvent,
        ToolCallCompleteEvent,
        PartStartEvent,
        PartDeltaEvent,
        StepCompletedEvent,
    ]
    assert tracker.done is True
    assert tracker.failed is False
    assert tracker.finish_reason == "stop"
    assert tracker.total_cost == Decimal("0.0025")
    assert tracker.total_usage.input_tokens == 20
    assert tracker.total_usage.output_tokens == 10
    assert emitted[2].kind == "execute"
    assert emitted[2].title == "`echo hi`"
    assert emitted[4].tool_result == "hi\n"


def test_compaction_retry_toast_and_errors() -> None:
    tracker = V2EventTracker(session_id="ses_1", agent_name="oc", message_id="m1")
    events = [
        _ev(
            SessionCompactionStarted,
            SessionCompactionStartedData(session_id="ses_1", reason="auto"),
        ),
        _ev(
            SessionCompactionEnded,
            SessionCompactionEndedData(session_id="ses_1", reason="auto", text="summary"),
        ),
        _ev(
            SessionRetryScheduled,
            SessionRetryScheduledData(
                session_id="ses_1",
                assistant_message_id="msg_1",
                attempt=1,
                at=9_999_999_999_999,
                error=SessionStructuredError(type="RateLimit", message="429"),
            ),
        ),
        _ev(
            TuiToastShow,
            TuiToastShowData(title="Heads up", message="Slow backend", variant="warning"),
        ),
        _ev(
            SessionToolFailed,
            SessionToolFailedData(
                session_id="ses_1",
                assistant_message_id="msg_1",
                id="call_err",
                error=SessionStructuredError(type="ToolError", message="boom"),
                executed=True,
            ),
        ),
        _ev(
            SessionExecutionFailed,
            SessionExecutionFailedData(
                session_id="ses_1",
                error=SessionStructuredError(type="ModelError", message="fatal"),
            ),
        ),
    ]
    emitted = [native for ev in events for native in tracker.handle(ev)]
    assert [type(e) for e in emitted] == [
        CompactionEvent,
        CompactionEvent,
        RetryEvent,
        NoticeEvent,
        ToolCallStartEvent,
        ToolCallCompleteEvent,
        RunErrorEvent,
    ]
    assert emitted[0].phase == "starting"
    assert emitted[1].phase == "completed"
    assert emitted[3].severity == "warning"
    assert emitted[5].error == "boom"
    assert emitted[6].message == "fatal"
    assert tracker.done is True
    assert tracker.failed is True
