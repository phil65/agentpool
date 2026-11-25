"""Protocol-level tests: spec coverage, generated models, pagination, projection."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter
from pydantic_ai.usage import RequestUsage
import pytest

from agentpool.agents.events import (
    PartStartEvent,
    RetryEvent,
    StepCompletedEvent,
    StepStartedEvent,
    TaskFinishedEvent,
    TaskStartedEvent,
)
from agentpool_server.opencode_v2_server.errors import ApiError
from agentpool_server.opencode_v2_server.events import build_event, new_session_id
from agentpool_server.opencode_v2_server.pagination import paginate
from agentpool_server.opencode_v2_server.projector import SessionProjection
from agentpool_server.opencode_v2_server.run_converter import RunEventConverter
from agentpool_server.opencode_v2_server.tasks import TaskTracker
from opencode_v2_sdk import models
from opencode_v2_sdk._version import OPENCODE_VERSION


if TYPE_CHECKING:
    from fastapi import FastAPI


SPEC = Path(__file__).parents[3] / "schema" / "opencode-v2" / "openapi.json"
METHODS = {"get", "post", "put", "patch", "delete"}


def _normalize(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path).replace("/*", "/{}")


def test_every_spec_operation_has_a_route(app: FastAPI) -> None:
    spec = json.loads(SPEC.read_text())
    served = {
        (method.upper(), _normalize(path))
        for path, item in app.openapi()["paths"].items()
        for method in item
    }
    served.add(("GET", "/api/pty/{}/connect"))  # websocket route, not part of FastAPI's schema
    missing = [
        f"{method.upper()} {path}"
        for path, item in spec["paths"].items()
        for method in item
        if method in METHODS and (method.upper(), _normalize(path)) not in served
    ]
    assert missing == []


def test_pinned_version_matches_spec() -> None:
    assert OPENCODE_VERSION == "2.4.1"
    assert len(json.loads(SPEC.read_text())["paths"]) > 100


def test_message_union_validates_by_type() -> None:
    adapter: TypeAdapter[models.SessionMessageInfo] = TypeAdapter(models.SessionMessageInfo)
    user = adapter.validate_python({
        "type": "user",
        "id": "msg_1",
        "text": "hi",
        "time": {"created": 1},
    })
    assert isinstance(user, models.SessionMessageUser)
    with pytest.raises(ValueError, match="extra"):
        adapter.validate_python({
            "type": "user",
            "id": "msg_1",
            "text": "hi",
            "time": {"created": 1},
            "unexpected": True,
        })


def test_free_form_metadata_is_kept() -> None:
    body = models.SessionCreateBody.model_validate({"metadata": {"source": "test", "n": 1}})
    assert body.metadata == {"source": "test", "n": 1}
    assert models.SessionCreateBody.model_fields["id"].annotation == str | None


def test_pagination_walks_both_directions() -> None:
    items = [f"msg_{i}" for i in range(5)]
    first = paginate(items, key=str, limit=2, order="asc", cursor=None)
    assert first.items == ["msg_0", "msg_1"]
    second = paginate(items, key=str, limit=2, order=None, cursor=first.next)
    assert second.items == ["msg_2", "msg_3"]
    back = paginate(items, key=str, limit=2, order=None, cursor=second.previous)
    assert back.items == ["msg_0", "msg_1"]
    newest = paginate(items, key=str, limit=2, order=None, cursor=None)
    assert newest.items == ["msg_4", "msg_3"]
    with pytest.raises(ApiError):
        paginate(items, key=str, limit=2, order="asc", cursor=first.next)


def _event(event_cls: type[Any], data: dict[str, Any], seq: int, sid: str) -> Any:
    return build_event(
        event_cls,
        event_cls.model_fields["data"].annotation.model_validate(data),
        aggregate_id=sid,
        seq=seq,
    )


def test_projection_builds_assistant_message() -> None:
    sid = new_session_id()
    projection = SessionProjection(session_id=sid)
    model = {"id": "m", "providerID": "p"}
    base = {"sessionID": sid, "assistantMessageID": "msg_a"}
    for seq, (event_cls, data) in enumerate(
        [
            (models.SessionStepStarted, {**base, "agent": "x", "model": model, "started": 1}),
            (models.SessionTextStarted, {**base, "ordinal": 0}),
            (models.SessionTextEnded, {**base, "ordinal": 0, "text": "Hello"}),
            (models.SessionToolInputStarted, {**base, "id": "call_1", "name": "read"}),
            (models.SessionToolInputEnded, {**base, "id": "call_1", "text": "{}"}),
            (models.SessionToolCalled, {**base, "id": "call_1", "input": {}, "executed": True}),
            (
                models.SessionToolFailed,
                {
                    **base,
                    "id": "call_1",
                    "executed": True,
                    "error": {"type": "ToolError", "message": "boom"},
                },
            ),
        ],
        start=1,
    ):
        projection.apply(_event(event_cls, data, seq, sid))
    (assistant,) = projection.messages
    assert isinstance(assistant, models.SessionMessageAssistant)
    text, tool = assistant.content
    assert isinstance(text, models.SessionMessageAssistantText)
    assert text.text == "Hello"
    assert isinstance(tool, models.SessionMessageAssistantTool)
    assert isinstance(tool.state, models.SessionMessageToolStateError)
    assert tool.state.error.message == "boom"


def test_retry_is_shown_on_the_retried_step() -> None:
    sid = new_session_id()
    emitted: list[tuple[type[Any], Any]] = []
    converter = RunEventConverter(
        session_id=sid,
        agent_id="claude",
        model_ref=models.ModelRef.model_validate({"id": "m", "providerID": "p"}),
        emit=lambda cls, data: emitted.append((cls, data)),
        clock=lambda: 1000,
    )
    converter.handle(RetryEvent(attempt=1, max_retries=3, delay_ms=500, error="overloaded"))
    converter.handle(StepStartedEvent(step_id="msg_api", model_name="m"))
    converter.handle(PartStartEvent.text(index=0, content="Hi"))
    converter.handle(StepCompletedEvent(step_id="msg_api", usage=RequestUsage(), model_name="m"))
    converter.finish()
    kinds = [cls for cls, _ in emitted]
    assert kinds.count(models.SessionStepStarted) == 1
    (retry,) = [data for cls, data in emitted if cls is models.SessionRetryScheduled]
    (started,) = [data for cls, data in emitted if cls is models.SessionStepStarted]
    assert retry.assistant_message_id == started.assistant_message_id
    assert (retry.attempt, retry.at, retry.error.message) == (1, 1500, "overloaded")


def test_task_changes_are_reported() -> None:
    changes: list[models.JobShell | models.JobSubagent] = []
    tracker = TaskTracker(session_id="ses_1", on_change=changes.append)
    tracker.observe(TaskStartedEvent(task_id="t1", kind="agent", description="Count"))
    tracker.observe(TaskFinishedEvent(task_id="t1", status="cancelled"))
    tracker.observe(TaskFinishedEvent(task_id="unknown", status="completed"))
    assert [(job.id, job.status) for job in changes] == [("t1", "running"), ("t1", "cancelled")]


def test_tasks_are_listed_as_jobs() -> None:
    tracker = TaskTracker(session_id="ses_1")
    tracker.observe(
        TaskStartedEvent(task_id="t1", kind="agent", description="Count", agent_type="Explore")
    )
    tracker.observe(TaskStartedEvent(task_id="t2", kind="shell", description="npm test"))
    tracker.observe(TaskStartedEvent(task_id="t3", kind="agent", description="Scan"))
    tracker.observe(TaskFinishedEvent(task_id="t1", status="completed"))
    tracker.observe(TaskFinishedEvent(task_id="t2", status="failed"))
    subagent, shell, running = tracker.jobs()
    assert isinstance(subagent, models.JobSubagent)
    assert (subagent.agent, subagent.status, subagent.child_session_id) == (
        "Explore",
        "completed",
        "ses_1",
    )
    assert subagent.time.completed is not None
    assert isinstance(shell, models.JobShell)
    assert (shell.command, shell.status) == ("npm test", "error")
    assert running.status == "running"
    # Once an execution has seen the results, finished tasks are no longer listed
    tracker.consume_finished()
    assert [job.id for job in tracker.jobs()] == ["t3"]
    tracker.observe(TaskFinishedEvent(task_id="t3", status="completed"))
    tracker.consume_finished()
    assert tracker.jobs() == []
