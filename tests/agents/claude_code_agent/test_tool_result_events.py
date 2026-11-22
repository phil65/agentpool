"""What Claude Code's structured tool results show beyond their text, as events."""

from __future__ import annotations

from typing import Any

import pytest

from agentpool.agents.claude_code_agent.converters import tool_result_events
from agentpool.agents.events import DiffContentItem, PlanUpdateEvent, ToolCallProgressEvent
from agentpool.utils.todos import PlanEntry


def _diff(events: list[Any]) -> DiffContentItem:
    [event] = events
    assert isinstance(event, ToolCallProgressEvent)
    assert event.replace_content  # The whole file replaces the snippet the start showed
    [item] = event.items
    assert isinstance(item, DiffContentItem)
    return item


def test_edit_shows_the_whole_file_before_and_after() -> None:
    result = {
        "filePath": "/a.py",
        "oldString": "x = 1",
        "newString": "x = 2",
        "originalFile": "x = 1\ny = x\n",
        "structuredPatch": [],
        "userModified": False,
        "replaceAll": False,
    }
    diff = _diff(tool_result_events("t1", "Edit", result))
    assert diff == DiffContentItem(
        path="/a.py", old_text="x = 1\ny = x\n", new_text="x = 2\ny = x\n"
    )


def test_edit_with_replace_all() -> None:
    result = {
        "filePath": "/a.py",
        "oldString": "a",
        "newString": "b",
        "originalFile": "a a",
        "structuredPatch": [],
        "userModified": False,
        "replaceAll": True,
    }
    assert _diff(tool_result_events("t1", "Edit", result)).new_text == "b b"


@pytest.mark.parametrize(
    ("kind", "original", "expected_old"),
    [("create", None, None), ("update", "old\n", "old\n")],
)
def test_write_shows_what_the_file_held(
    kind: str, original: str | None, expected_old: str | None
) -> None:
    result = {
        "type": kind,
        "filePath": "/b.txt",
        "content": "new\n",
        "structuredPatch": [],
        "originalFile": original,
    }
    diff = _diff(tool_result_events("t1", "Write", result))
    assert (diff.old_text, diff.new_text) == (expected_old, "new\n")


def test_todowrite_becomes_the_plan() -> None:
    todos = [
        {"content": "Write tests", "status": "completed", "activeForm": "Writing tests"},
        {"content": "Fix bug", "status": "in_progress", "activeForm": "Fixing bug"},
    ]
    [event] = tool_result_events("t1", "TodoWrite", {"oldTodos": [], "newTodos": todos})
    assert event == PlanUpdateEvent(
        entries=[
            PlanEntry(content="Write tests", status="completed"),
            PlanEntry(content="Fix bug", status="in_progress"),
        ],
        tool_call_id="t1",
    )


def _read(start: int, count: int, total: int) -> dict[str, Any]:
    file = {
        "filePath": "/c.py",
        "content": "",
        "numLines": count,
        "startLine": start,
        "totalLines": total,
    }
    return {"type": "text", "file": file}


def test_a_partial_read_says_which_lines() -> None:
    [event] = tool_result_events("t1", "Read", _read(11, 20, 300))
    assert isinstance(event, ToolCallProgressEvent)
    assert event.title == "Read /c.py (lines 11-30 of 300)"


def test_a_whole_read_shows_nothing_more() -> None:
    assert tool_result_events("t1", "Read", _read(1, 300, 300)) == []


@pytest.mark.parametrize("result", [None, "Error: failed", {"unexpected": True}])
def test_results_without_more_to_show(result: Any) -> None:
    assert tool_result_events("t1", "Edit", result) == []
    assert tool_result_events("t1", "Grep", {"filenames": []}) == []
