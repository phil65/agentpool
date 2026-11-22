"""OpenCode's tool metadata, translated into diffs and the plan."""

from __future__ import annotations

from typing import Any

from agentpool.agents.events import DiffContentItem, PlanUpdateEvent, ToolCallProgressEvent
from agentpool.agents.opencode_v2_agent.events import tool_metadata_events
from agentpool.utils.todos import PlanEntry


def _diffs(events: list[Any]) -> list[DiffContentItem]:
    [event] = events
    assert isinstance(event, ToolCallProgressEvent)
    return [item for item in event.items if isinstance(item, DiffContentItem)]


def _filediff(path: str, before: str, after: str, **extra: Any) -> dict[str, Any]:
    return {"file": path, "before": before, "after": after, "additions": 1, "deletions": 1, **extra}


def test_edit_becomes_a_diff() -> None:
    metadata = {"diff": "", "filediff": _filediff("/a.py", "x\n", "y\n"), "diagnostics": {}}
    assert _diffs(tool_metadata_events("t", "edit", {}, metadata)) == [
        DiffContentItem(path="/a.py", old_text="x\n", new_text="y\n")
    ]


def test_multiedit_is_one_diff_per_file_from_first_to_last() -> None:
    results = [
        {"filediff": _filediff("/a.py", "1", "2")},
        {"filediff": _filediff("/a.py", "2", "3")},
    ]
    assert _diffs(tool_metadata_events("t", "multiedit", {}, {"results": results})) == [
        DiffContentItem(path="/a.py", old_text="1", new_text="3")
    ]


def test_apply_patch_files_become_diffs() -> None:
    def file(path: str, kind: str, before: str, after: str, **extra: Any) -> dict[str, Any]:
        info = {"filePath": path, "relativePath": path, "type": kind, "diff": ""}
        return {**info, "before": before, "after": after, "additions": 0, "deletions": 0, **extra}

    files = [
        file("/new.py", "add", "", "new"),
        file("/old.py", "move", "same", "same", movePath="/moved.py"),
    ]
    metadata = {"diff": "", "files": files, "diagnostics": {}}
    assert _diffs(tool_metadata_events("t", "apply_patch", {}, metadata)) == [
        DiffContentItem(path="/new.py", old_text=None, new_text="new"),
        DiffContentItem(path="/moved.py", old_text="same", new_text="same"),
    ]


def test_write_of_a_new_file_becomes_a_diff() -> None:
    metadata = {"filepath": "/b.txt", "exists": False, "diagnostics": {}}
    assert _diffs(tool_metadata_events("t", "write", {"content": "hi"}, metadata)) == [
        DiffContentItem(path="/b.txt", old_text=None, new_text="hi")
    ]
    # Overwritten: what the file held is unknown, so no diff is made up
    overwritten = {"filepath": "/b.txt", "exists": True, "diagnostics": {}}
    assert tool_metadata_events("t", "write", {"content": "hi"}, overwritten) == []


def test_todos_become_the_plan_with_their_priority_and_status() -> None:
    todos = [
        {"content": "a", "status": "cancelled", "priority": "high"},
        {"content": "b", "status": "weird", "priority": "weird"},
    ]
    [event] = tool_metadata_events("t", "todowrite", {}, {"todos": todos})
    assert event == PlanUpdateEvent(
        entries=[
            PlanEntry(content="a", status="cancelled", priority="high"),
            PlanEntry(content="b", status="pending", priority="medium"),
        ],
        tool_call_id="t",
    )


def test_other_metadata_shows_nothing_more() -> None:
    assert tool_metadata_events("t", "bash", {}, {"output": "ok", "exit": 0}) == []
    assert tool_metadata_events("t", "read", {}, None) == []
