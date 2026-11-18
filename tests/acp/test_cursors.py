"""Tests for session cursor models."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError
import pytest

from acp.schema import (
    AgentMessageCursorAnchor,
    ClientSessionCapabilities,
    CompactionCursorAnchor,
    CursorUpdate,
    ForkSessionRequest,
    SessionCursorsCapabilities,
    SessionUpdate,
    ToolCallCursorAnchor,
    UnknownCursorAnchor,
)


UPDATES: TypeAdapter[Any] = TypeAdapter(SessionUpdate)


def _cursor_update(anchor: dict[str, Any]) -> CursorUpdate:
    update = UPDATES.validate_python({
        "sessionUpdate": "cursor_update",
        "cursorId": "cur_1",
        "anchor": anchor,
        "capabilities": {"fork": {}},
    })
    assert isinstance(update, CursorUpdate)
    return update


def test_cursor_update_with_message_anchor() -> None:
    update = _cursor_update({"type": "agent_message", "messageId": "msg_1", "position": "after"})

    assert update.cursor_id == "cur_1"
    assert isinstance(update.anchor, AgentMessageCursorAnchor)
    assert update.anchor.message_id == "msg_1"
    assert update.anchor.position == "after"
    assert update.capabilities is not None
    assert update.capabilities.fork is not None


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        ({"type": "tool_call", "toolCallId": "tc_1", "position": "before"}, ToolCallCursorAnchor),
        ({"type": "compaction", "compactionId": "c1", "position": "after"}, CompactionCursorAnchor),
        ({"type": "_custom", "whatever": 1}, UnknownCursorAnchor),
    ],
)
def test_cursor_anchor_kinds(anchor: dict[str, Any], expected: type) -> None:
    assert isinstance(_cursor_update(anchor).anchor, expected)


def test_unknown_anchor_keeps_its_type() -> None:
    anchor = _cursor_update({"type": "_custom"}).anchor

    assert isinstance(anchor, UnknownCursorAnchor)
    assert anchor.type == "_custom"


def test_malformed_known_anchor_does_not_fall_back() -> None:
    with pytest.raises(ValidationError):
        _cursor_update({"type": "tool_call", "position": "before"})  # no toolCallId


def test_later_update_may_omit_anchor() -> None:
    update = UPDATES.validate_python({"sessionUpdate": "cursor_update", "cursorId": "cur_1"})

    assert isinstance(update, CursorUpdate)
    assert update.anchor is None
    assert update.capabilities is None


def test_cursor_update_serializes_to_wire_names() -> None:
    update = CursorUpdate(
        cursor_id="cur_1",
        anchor=ToolCallCursorAnchor(tool_call_id="tc_1", position="after"),
    )

    assert update.model_dump(mode="json", by_alias=True, exclude_none=True) == {
        "sessionUpdate": "cursor_update",
        "cursorId": "cur_1",
        "anchor": {"type": "tool_call", "toolCallId": "tc_1", "position": "after"},
    }


def test_fork_at_cursor_and_client_capability() -> None:
    request = ForkSessionRequest.model_validate({
        "sessionId": "s_1",
        "cwd": "/tmp/project",
        "cursorId": "cur_1",
    })
    caps = ClientSessionCapabilities.model_validate({"cursors": {}})

    assert request.cursor_id == "cur_1"
    assert isinstance(caps.cursors, SessionCursorsCapabilities)
