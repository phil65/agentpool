"""Protocol-level tests: spec coverage, generated models, pagination, projection."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter
import pytest

from agentpool_server.opencode_v2_server.errors import ApiError
from agentpool_server.opencode_v2_server.events import build_event, new_session_id
from agentpool_server.opencode_v2_server.pagination import paginate
from agentpool_server.opencode_v2_server.projector import SessionProjection
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
    assert OPENCODE_VERSION == "2.3.2"
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
