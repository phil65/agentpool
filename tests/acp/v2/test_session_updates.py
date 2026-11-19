"""Tests for v2 session updates."""

from __future__ import annotations

from typing import Any

from pydantic import MISSING, TypeAdapter

from acp.schema.v2.base import to_wire
from acp.schema.v2.content import TextContent
from acp.schema.v2.messages import AgentMessageChunk
from acp.schema.v2.session_updates import (
    IdleStateSessionUpdate,
    RunningStateSessionUpdate,
    SessionUpdate,
    UnknownSessionUpdate,
    UpdateSessionNotification,
)
from acp.schema.v2.terminals import TerminalUpdate
from acp.schema.v2.tool_calls import (
    AddDiffChange,
    Diff,
    MoveDiffChange,
    ToolCallUpdate,
    UnknownDiffChange,
)


UPDATES: TypeAdapter[Any] = TypeAdapter(SessionUpdate)


def test_state_update_inlines_the_state() -> None:
    update = UPDATES.validate_python({
        "sessionUpdate": "state_update",
        "state": "idle",
        "stopReason": "end_turn",
    })

    assert isinstance(update, IdleStateSessionUpdate)
    assert update.stop_reason == "end_turn"
    assert to_wire(RunningStateSessionUpdate()) == {
        "state": "running",
        "sessionUpdate": "state_update",
    }


def test_tool_call_update_patch_clears_title() -> None:
    update = UPDATES.validate_python({
        "sessionUpdate": "tool_call_update",
        "toolCallId": "t1",
        "title": None,
        "status": "completed",
    })

    assert isinstance(update, ToolCallUpdate)
    assert update.title is None  # cleared
    assert update.kind is MISSING  # unchanged
    assert to_wire(update) == {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "t1",
        "title": None,
        "status": "completed",
    }


def test_diff_changes_by_operation() -> None:
    update = UPDATES.validate_python({
        "sessionUpdate": "tool_call_update",
        "toolCallId": "t1",
        "content": [
            {
                "type": "diff",
                "changes": [
                    {"operation": "add", "path": "/a.py", "fileType": "text"},
                    {"operation": "move", "oldPath": "/b.py", "path": "/c.py"},
                    {"operation": "_chmod", "path": "/d.py", "mode": "755"},
                ],
                "patch": {"format": "git_patch", "text": "diff --git a/a.py b/a.py"},
            }
        ],
    })

    assert isinstance(update, ToolCallUpdate)
    assert update.content is not MISSING
    assert update.content is not None
    diff = update.content[0]
    assert isinstance(diff, Diff)
    assert [type(c) for c in diff.changes] == [AddDiffChange, MoveDiffChange, UnknownDiffChange]


def test_terminal_update() -> None:
    update = UPDATES.validate_python({
        "sessionUpdate": "terminal_update",
        "terminalId": "term1",
        "command": "ls",
        "cwd": "/work",
        "size": {"cols": 80, "rows": 24},
        "exitStatus": {"exitCode": 0},
    })

    assert isinstance(update, TerminalUpdate)
    assert update.exit_status is not MISSING
    assert update.exit_status is not None
    assert update.exit_status.exit_code == 0


def test_unknown_session_update_round_trips() -> None:
    payload = {"sessionUpdate": "_x_progress", "percent": 50}

    update = UPDATES.validate_python(payload)

    assert isinstance(update, UnknownSessionUpdate)
    assert to_wire(update) == payload


def test_session_notification_serialization() -> None:
    notification = UpdateSessionNotification(
        session_id="s1",
        update=AgentMessageChunk(message_id="m1", content=TextContent(text="Hi")),
    )

    assert to_wire(notification) == {
        "sessionId": "s1",
        "update": {
            "messageId": "m1",
            "content": {"type": "text", "text": "Hi"},
            "sessionUpdate": "agent_message_chunk",
        },
    }
