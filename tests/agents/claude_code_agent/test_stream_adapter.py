"""Tests for the Claude Code stream adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clawd_code_sdk.models import PromptSuggestionMessage, StatusSystemMessage, message_adapter
from pydantic_ai import TextPart

from agentpool.agents.claude_code_agent.server_blocks import REDACTED_THINKING
from agentpool.agents.claude_code_agent.stream_adapter import ClaudeCodeStreamedResponse
from agentpool.agents.events import (
    CompactionEvent,
    DiffContentItem,
    NoticeEvent,
    PartStartEvent,
    PromptSuggestionEvent,
    RetryEvent,
    SubAgentEvent,
    TaskFinishedEvent,
    TaskStartedEvent,
    TaskUpdatedEvent,
    TaskUsage,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _stream(*messages: Any) -> list[Any]:
    async def source() -> AsyncIterator[Any]:
        for message in messages:
            yield message

    response = ClaudeCodeStreamedResponse(
        provider_name="claude_code",
        stream=source(),
        agent_name="claude",
        session_id="local",
    )
    return [event async for event in response._get_event_iterator()]


async def test_failed_compaction() -> None:
    events = await _stream(
        StatusSystemMessage(uuid="1", session_id="s", status="compacting"),
        StatusSystemMessage(
            uuid="2", session_id="s", status=None, compact_result="failed", compact_error="boom"
        ),
    )
    assert events == [
        CompactionEvent(session_id="local", phase="starting"),
        CompactionEvent(session_id="local", phase="failed", error="boom"),
    ]


async def test_prompt_suggestion() -> None:
    message = PromptSuggestionMessage(uuid="1", session_id="s", suggestion="run tests")
    assert await _stream(message) == [PromptSuggestionEvent(suggestions=["run tests"])]


def _frame(data: dict[str, Any]) -> Any:
    return message_adapter.validate_python({"session_id": "s", "uuid": "u", **data})


def _assistant(*blocks: dict[str, Any], parent: str | None = None, **extra: Any) -> Any:
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "stop_reason": None,
        "stop_sequence": None,
        "content": list(blocks),
    }
    return _frame({"type": "assistant", "parent_tool_use_id": parent, "message": message, **extra})


def _user(*blocks: dict[str, Any], parent: str | None = None, **extra: Any) -> Any:
    message = {"role": "user", "content": list(blocks)}
    return _frame({"type": "user", "parent_tool_use_id": parent, "message": message, **extra})


def _block_start(index: int, block: dict[str, Any]) -> Any:
    event = {"type": "content_block_start", "index": index, "content_block": block}
    return _frame({"type": "stream_event", "parent_tool_use_id": None, "event": event})


def _tool_use(tc_id: str, name: str, **input_: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": tc_id, "name": name, "input": input_}


def _tool_result(tc_id: str, content: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": tc_id, "content": content, "is_error": is_error}


def _system(subtype: str, **data: Any) -> Any:
    return _frame({"type": "system", "subtype": subtype, **data})


async def test_subagent_frames_are_wrapped() -> None:
    events = await _stream(
        _block_start(0, _tool_use("call_agent", "Agent")),
        _assistant(_tool_use("call_agent", "Agent", subagent_type="Explore", prompt="count")),
        _system(
            "task_started",
            task_id="t1",
            tool_use_id="call_agent",
            description="Count files",
            subagent_type="Explore",
            spawn_depth=1,
        ),
        _user({"type": "text", "text": "count"}, parent="call_agent"),
        _assistant(
            {"type": "text", "text": "Counting now."},
            _tool_use("call_sub", "Bash", command="ls | wc -l"),
            parent="call_agent",
            subagent_type="Explore",
        ),
        _system(
            "task_progress",
            task_id="t1",
            tool_use_id="call_agent",
            description="Count files",
            usage={"total_tokens": 10, "tool_uses": 1, "duration_ms": 5},
            last_tool_name="Bash",
        ),
        _user(_tool_result("call_sub", "3"), parent="call_agent"),
        _user(_tool_result("call_agent", "There are 3 files.")),
        _system(
            "task_notification",
            task_id="t1",
            tool_use_id="call_agent",
            status="completed",
            output_file="/tmp/t1.output",
            summary="3 files",
            usage={"total_tokens": 20, "tool_uses": 1, "duration_ms": 9},
        ),
    )
    task_events = [
        e for e in events if isinstance(e, TaskStartedEvent | TaskUpdatedEvent | TaskFinishedEvent)
    ]
    assert task_events == [
        TaskStartedEvent(
            task_id="t1",
            kind="agent",
            description="Count files",
            tool_call_id="call_agent",
            agent_type="Explore",
        ),
        TaskUpdatedEvent(
            task_id="t1",
            message="Running Bash",
            last_tool="Bash",
            usage=TaskUsage(total_tokens=10, tool_uses=1, duration_ms=5),
        ),
        TaskFinishedEvent(
            task_id="t1",
            status="completed",
            summary="3 files",
            output_ref="/tmp/t1.output",
            usage=TaskUsage(total_tokens=20, tool_uses=1, duration_ms=9),
        ),
    ]
    sub_events = [e for e in events if isinstance(e, SubAgentEvent)]
    assert {(e.source_name, e.depth, e.parent_tool_call_id) for e in sub_events} == {
        ("Explore", 1, "call_agent")
    }
    inner = [e.event for e in sub_events]
    assert isinstance(inner[0], PartStartEvent)
    assert inner[0].part == TextPart(content="Counting now.")
    assert isinstance(inner[1], ToolCallStartEvent)
    assert inner[1].tool_call_id == "call_sub"
    assert isinstance(inner[2], ToolCallCompleteEvent)
    assert (inner[2].tool_call_id, inner[2].tool_result) == ("call_sub", "3")
    # The main conversation only sees its own tool call
    main = [e for e in events if not isinstance(e, SubAgentEvent)]
    starts = [e.tool_call_id for e in main if isinstance(e, ToolCallStartEvent)]
    completes = [e.tool_call_id for e in main if isinstance(e, ToolCallCompleteEvent)]
    assert starts == completes == ["call_agent"]
    (task_progress,) = [
        e for e in main if isinstance(e, ToolCallProgressEvent) and e.message is not None
    ]
    assert task_progress.tool_call_id == "call_agent"
    assert task_progress.message == "Running Bash"
    assert task_progress.field_meta == {
        "task_usage": {"total_tokens": 10, "tool_uses": 1, "duration_ms": 5}
    }


async def test_server_tool_lifecycle() -> None:
    server_use = {"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {}}
    search_error = {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}
    events = await _stream(
        _block_start(0, server_use),
        _assistant({**server_use, "input": {"query": "python"}}),
        _assistant({"type": "web_search_tool_result", "tool_use_id": "srv_1", "content": []}),
        _block_start(1, {**server_use, "id": "srv_2"}),
        _assistant({**server_use, "id": "srv_2", "input": {"query": "again"}}),
        _assistant({
            "type": "web_search_tool_result",
            "tool_use_id": "srv_2",
            "content": search_error,
        }),
    )
    start, progress, complete, _, _, failed = events
    assert isinstance(start, ToolCallStartEvent)
    assert (start.tool_call_id, start.tool_name, start.kind) == ("srv_1", "web_search", "search")
    assert isinstance(progress, ToolCallProgressEvent)
    assert progress.tool_input == {"query": "python"}
    assert isinstance(complete, ToolCallCompleteEvent)
    assert (complete.tool_call_id, complete.tool_input, complete.error) == (
        "srv_1",
        {"query": "python"},
        None,
    )
    assert isinstance(failed, ToolCallCompleteEvent)
    assert failed.error == "max_uses_exceeded"


async def test_mcp_connector_tool_without_stream() -> None:
    events = await _stream(
        _assistant({
            "type": "mcp_tool_use",
            "id": "mcp_1",
            "name": "search",
            "server_name": "docs",
            "input": {"q": "x"},
        }),
        _assistant({
            "type": "mcp_tool_result",
            "tool_use_id": "mcp_1",
            "is_error": True,
            "content": [{"type": "text", "text": "not found"}],
        }),
    )
    start, complete = events
    assert isinstance(start, ToolCallStartEvent)
    assert start.title == "docs: search"
    assert isinstance(complete, ToolCallCompleteEvent)
    assert (complete.tool_name, complete.error) == ("search", "not found")


async def test_redacted_thinking_and_compaction_blocks() -> None:
    events = await _stream(
        _block_start(0, {"type": "redacted_thinking", "data": "abc"}),
        _assistant({"type": "redacted_thinking", "data": "abc"}),
        _assistant({"type": "compaction", "content": "summary"}),
        _assistant({"type": "container_upload", "file_id": "file_1"}),
    )
    assert events == [
        PartStartEvent.thinking(index=0, content=REDACTED_THINKING),
        CompactionEvent(session_id="local", trigger="auto", phase="completed"),
    ]


async def test_notices() -> None:
    def rate_limit(status: str) -> Any:
        info = {"status": status, "rateLimitType": "five_hour", "utilization": 0.9}
        return _frame({"type": "rate_limit_event", "rate_limit_info": info})

    events = await _stream(
        _system(
            "model_refusal_fallback",
            trigger="refusal",
            direction="retry",
            original_model="claude-opus-5",
            fallback_model="claude-sonnet-5",
            request_id=None,
            content="",
        ),
        _system(
            "permission_denied",
            tool_name="Bash",
            tool_use_id="t",
            decision_reason="rule: deny",
            message="Permission denied",
        ),
        _system(
            "hook_response",
            hook_id="h",
            hook_name="lint",
            hook_event="PostToolUse",
            outcome="success",
            stderr="",
            stdout="",
            output="",
        ),
        rate_limit("allowed"),
        rate_limit("allowed_warning"),
        rate_limit("rejected"),
    )
    assert events == [
        NoticeEvent(
            title="Switched from claude-opus-5 to claude-sonnet-5 after a refusal",
            severity="warning",
        ),
        NoticeEvent(title="Permission denied: Bash", severity="warning", description="rule: deny"),
        NoticeEvent(title="Rate limit close: five hour (90% used)", severity="warning"),
        NoticeEvent(title="Rate limit reached: five hour (90% used)", severity="error"),
    ]


async def test_api_retry() -> None:
    events = await _stream(
        _system(
            "api_retry",
            attempt=2,
            max_retries=10,
            retry_delay_ms=1500.0,
            error_status=529,
            error="server_error",
        )
    )
    assert events == [
        RetryEvent(
            attempt=2, max_retries=10, delay_ms=1500.0, error="server error", status_code=529
        )
    ]


async def test_tool_result_without_tool_call_is_skipped() -> None:
    # A result for a call this run never started (e.g. after an interrupted run)
    events = await _stream(_user(_tool_result("unknown", "interrupted", is_error=True)))
    assert events == []


async def test_bash_result_uses_the_structured_output() -> None:
    command = "ls /nope; exit 3"
    text = "Exit code 3\nls: cannot access '/nope': No such file or directory"
    events = await _stream(
        _assistant(_tool_use("call_1", "Bash", command=command, description="List")),
        # One tool result: the CLI sends tool_use_result as a single value (a string on failure)
        _user(_tool_result("call_1", text, is_error=True), tool_use_result=f"Error: {text}"),
    )
    output, exit_, complete = [e for e in events if e.tool_call_id == "call_1"][-3:]
    assert isinstance(output, ToolCallProgressEvent)
    assert output.field_meta == {
        "terminal_output": {
            "terminal_id": "call_1",
            "data": "ls: cannot access '/nope': No such file or directory",
        }
    }
    assert isinstance(exit_, ToolCallProgressEvent)
    assert exit_.field_meta == {
        "terminal_exit": {"terminal_id": "call_1", "exit_code": 3, "signal": None}
    }
    assert isinstance(complete, ToolCallCompleteEvent)


async def test_edit_shows_the_whole_file_from_a_single_structured_result() -> None:
    structured = {
        "filePath": "/a.py",
        "oldString": "x = 1",
        "newString": "x = 2",
        "originalFile": "x = 1\n",
        "structuredPatch": [
            {
                "oldStart": 1,
                "oldLines": 1,
                "newStart": 1,
                "newLines": 1,
                "lines": ["-x = 1", "+x = 2"],
            }
        ],
        "userModified": False,
        "replaceAll": False,
    }
    events = await _stream(
        _assistant(
            _tool_use("call_1", "Edit", file_path="/a.py", old_string="x = 1", new_string="x = 2")
        ),
        _user(
            _tool_result("call_1", "The file /a.py has been updated."), tool_use_result=structured
        ),
    )
    *_, diff, complete = [e for e in events if e.tool_call_id == "call_1"]
    assert isinstance(diff, ToolCallProgressEvent)
    assert diff.replace_content  # The whole file instead of the snippet the start showed
    assert diff.items == [DiffContentItem(path="/a.py", old_text="x = 1\n", new_text="x = 2\n")]
    assert isinstance(complete, ToolCallCompleteEvent)


async def test_local_command_output_is_the_reply() -> None:
    # /context answers with a complete assistant message that never streamed (no model ran);
    # other local commands send local_command_output
    reply = _assistant({"type": "text", "text": "## Context Usage"})
    local = _system("local_command_output", content="Total cost: $0.01")
    events = await _stream(reply, local)
    texts = [e.part.content for e in events if isinstance(e, PartStartEvent)]
    assert texts == ["## Context Usage", "Total cost: $0.01"]


async def test_streamed_text_is_not_repeated() -> None:
    message_start = {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    }
    start = _frame({"type": "stream_event", "parent_tool_use_id": None, "event": message_start})
    events = await _stream(start, _assistant({"type": "text", "text": "already streamed"}))
    assert not [e for e in events if isinstance(e, PartStartEvent)]
