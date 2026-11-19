"""Tests for v2 session updates: messages, tool calls, terminals, plans and state."""

from __future__ import annotations

from typing import Any

from pydantic import MISSING, BaseModel, TypeAdapter
import pytest

from acp.schema.v2.base import to_wire
from acp.schema.v2.content import TextContent
from acp.schema.v2.cursors import CursorToolCallAnchor, CursorUpdate
from acp.schema.v2.messages import AgentMessage, AgentMessageChunk
from acp.schema.v2.plans import PlanItems, PlanUpdate
from acp.schema.v2.session_updates import (
    IdleStateSessionUpdate,
    RunningStateSessionUpdate,
    SessionUpdate,
    UnknownSessionUpdate,
    UpdateSessionNotification,
)
from acp.schema.v2.terminals import TerminalOutputChunk, TerminalUpdate
from acp.schema.v2.tool_calls import (
    Diff,
    ModifyDiffChange,
    MoveDiffChange,
    Terminal,
    ToolCallUpdate,
)


UPDATES: TypeAdapter[Any] = TypeAdapter(SessionUpdate)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "sessionUpdate": "agent_message_chunk",
                "messageId": "m1",
                "content": {"type": "text", "text": "hi"},
            },
            AgentMessageChunk,
        ),
        ({"sessionUpdate": "agent_message", "messageId": "m1", "content": None}, AgentMessage),
        ({"sessionUpdate": "state_update", "state": "running"}, RunningStateSessionUpdate),
        (
            {"sessionUpdate": "state_update", "state": "idle", "stopReason": "end_turn"},
            IdleStateSessionUpdate,
        ),
        ({"sessionUpdate": "tool_call_update", "toolCallId": "t1"}, ToolCallUpdate),
        ({"sessionUpdate": "terminal_update", "terminalId": "x"}, TerminalUpdate),
        ({"sessionUpdate": "cursor_update", "cursorId": "c1"}, CursorUpdate),
        ({"sessionUpdate": "_vendor_thing", "data": [1, 2]}, UnknownSessionUpdate),
    ],
)
def test_session_updates_round_trip(payload: dict[str, Any], expected: type[BaseModel]) -> None:
    update = UPDATES.validate_python(payload)

    assert isinstance(update, expected)
    assert to_wire(update) == payload


def test_tool_call_update_patch_semantics() -> None:
    clear_title = ToolCallUpdate(tool_call_id="t1", title=None, status="in_progress")

    assert to_wire(clear_title) == {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "t1",
        "title": None,
        "status": "in_progress",
    }
    assert clear_title.kind is MISSING


def test_tool_call_with_diff_and_terminal_content() -> None:
    update = UPDATES.validate_python({
        "sessionUpdate": "tool_call_update",
        "toolCallId": "t1",
        "content": [
            {
                "type": "diff",
                "changes": [
                    {"operation": "modify", "path": "/a.py", "fileType": "text"},
                    {"operation": "move", "oldPath": "/b.py", "path": "/c.py"},
                ],
                "patch": {"format": "git_patch", "text": "diff --git a/a.py b/a.py"},
            },
            {"type": "terminal", "terminalId": "term1"},
        ],
    })

    assert isinstance(update, ToolCallUpdate)
    content = update.content
    assert content is not MISSING
    assert content is not None
    diff, terminal = content
    assert isinstance(diff, Diff)
    assert [type(c) for c in diff.changes] == [ModifyDiffChange, MoveDiffChange]
    assert isinstance(terminal, Terminal)


def test_terminal_output_bytes() -> None:
    chunk = TerminalOutputChunk.from_bytes("term1", b"\x1b[32mok\x1b[0m\n")

    assert chunk.decode() == b"\x1b[32mok\x1b[0m\n"
    assert UPDATES.validate_python(to_wire(chunk)) == chunk


def test_plan_update_with_items() -> None:
    update = UPDATES.validate_python({
        "sessionUpdate": "plan_update",
        "plan": {
            "type": "items",
            "planId": "p1",
            "entries": [{"content": "write tests", "priority": "high", "status": "pending"}],
        },
    })

    assert isinstance(update, PlanUpdate)
    assert isinstance(update.plan, PlanItems)


def test_cursor_anchor_and_notification() -> None:
    notification = UpdateSessionNotification(
        session_id="s1",
        update=CursorUpdate(
            cursor_id="c1",
            anchor=CursorToolCallAnchor(tool_call_id="t1", position="after"),
        ),
    )

    assert to_wire(notification) == {
        "sessionId": "s1",
        "update": {
            "sessionUpdate": "cursor_update",
            "cursorId": "c1",
            "anchor": {"type": "tool_call", "toolCallId": "t1", "position": "after"},
        },
    }


def test_message_chunk_requires_message_id() -> None:
    with pytest.raises(ValueError, match="messageId"):
        UPDATES.validate_python({
            "sessionUpdate": "agent_message_chunk",
            "content": to_wire(TextContent(text="hi")),
        })
