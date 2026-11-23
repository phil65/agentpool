"""The v2 terminals a session reports (``TerminalTracker``)."""

from __future__ import annotations

from aclientp.schema import v2

from agentpool.agents.events import (
    TerminalContentItem,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool_server.acp_server.v2_terminals import TerminalTracker


def _complete(tool_call_id: str) -> ToolCallCompleteEvent:
    return ToolCallCompleteEvent(
        tool_name="bash",
        tool_call_id=tool_call_id,
        tool_input={},
        tool_result="",
        agent_name="a",
        message_id="m",
    )


def test_a_process_reports_command_output_and_exit() -> None:
    tracker = TerminalTracker(cwd="/work")
    started = ToolCallProgressEvent.process_started(
        tool_call_id="c1", process_id="p1", command="echo hi"
    )
    output = ToolCallProgressEvent.process_output(tool_call_id="c1", process_id="p1", output="hi\n")
    exited = ToolCallProgressEvent.process_exit(tool_call_id="c1", process_id="p1", exit_code=3)

    [start] = tracker.updates(started)
    [chunk] = tracker.updates(output)
    [end] = tracker.updates(exited)

    assert isinstance(start, v2.TerminalUpdate)
    assert (start.terminal_id, start.command, start.cwd) == ("p1", "echo hi", "/work")
    assert isinstance(chunk, v2.TerminalOutputChunk)
    assert chunk.decode() == b"hi\n"
    assert isinstance(end, v2.TerminalUpdate)
    assert isinstance(end.exit_status, v2.TerminalExitStatus)
    assert end.exit_status.exit_code == 3
    assert tracker.updates(_complete("c1")) == []  # Exited already


def test_output_snapshots_replace_the_output() -> None:
    tracker = TerminalTracker()
    tracker.updates(
        ToolCallProgressEvent.process_started(tool_call_id="c1", process_id="p1", command="x")
    )
    event = ToolCallProgressEvent.process_output(
        tool_call_id="c2", process_id="p1", output="all of it", snapshot=True
    )
    [update] = tracker.updates(event)
    assert isinstance(update, v2.TerminalUpdate)
    assert isinstance(update.output, v2.TerminalOutput)
    assert update.output.decode() == b"all of it"


def test_processes_outlive_their_tool_call_until_cancelled() -> None:
    tracker = TerminalTracker()
    tracker.updates(
        ToolCallProgressEvent.process_started(tool_call_id="c1", process_id="p1", command="x")
    )
    assert tracker.updates(_complete("c1")) == []  # A background process keeps running

    [ended] = tracker.end(["c1"])
    assert isinstance(ended, v2.TerminalUpdate)
    assert ended.terminal_id == "p1"
    assert isinstance(ended.exit_status, v2.TerminalExitStatus)


def test_referenced_terminals_exist_and_end_with_their_tool_call() -> None:
    tracker = TerminalTracker()
    start = ToolCallStartEvent(
        tool_call_id="c1",
        tool_name="nested",
        title="Run",
        content=[TerminalContentItem(terminal_id="t1")],
    )
    [created] = tracker.updates(start)
    assert isinstance(created, v2.TerminalUpdate)
    assert created.terminal_id == "t1"
    assert tracker.updates(start) == []  # Known now

    [ended] = tracker.updates(_complete("c1"))
    assert isinstance(ended, v2.TerminalUpdate)
    assert isinstance(ended.exit_status, v2.TerminalExitStatus)


def test_meta_display_terminals() -> None:
    """Claude Code's bash calls use the ``_meta`` display-terminal convention."""
    tracker = TerminalTracker()
    start = ToolCallStartEvent(
        tool_call_id="c1",
        tool_name="Bash",
        title="ls",
        content=[TerminalContentItem(terminal_id="c1")],
        field_meta={"terminal_info": {"terminal_id": "c1"}},
    )
    output = ToolCallProgressEvent(
        tool_call_id="c1", field_meta={"terminal_output": {"terminal_id": "c1", "data": "a b"}}
    )
    exited = ToolCallProgressEvent(
        tool_call_id="c1",
        field_meta={"terminal_exit": {"terminal_id": "c1", "exit_code": 0, "signal": None}},
    )

    assert [u.terminal_id for u in tracker.updates(start) if isinstance(u, v2.TerminalUpdate)] == [
        "c1"
    ]
    [snapshot] = tracker.updates(output)
    [end] = tracker.updates(exited)

    assert isinstance(snapshot, v2.TerminalUpdate)
    assert isinstance(snapshot.output, v2.TerminalOutput)
    assert snapshot.output.decode() == b"a b"
    assert isinstance(end, v2.TerminalUpdate)
    assert isinstance(end.exit_status, v2.TerminalExitStatus)
    assert end.exit_status.exit_code == 0


def test_process_titles_stay_the_command() -> None:
    started = ToolCallProgressEvent.process_started(
        tool_call_id="c1", process_id="p1", command="make"
    )
    output = ToolCallProgressEvent.process_output(tool_call_id="c1", process_id="p1", output="ok")
    exited = ToolCallProgressEvent.process_exit(tool_call_id="c1", process_id="p1", exit_code=0)
    assert started.title == "Running: make"
    assert output.title is None
    assert exited.title is None
