"""Tests for the Claude Code stream adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clawd_code_sdk.models import PromptSuggestionMessage, StatusSystemMessage, message_adapter
from pydantic_ai import TextPart

from agentpool.agents.claude_code_agent.server_blocks import REDACTED_THINKING
from agentpool.agents.claude_code_agent.stream_adapter import ClaudeCodeStreamedResponse
from agentpool.agents.events import (
    CompactionEvent,
    NoticeEvent,
    PartStartEvent,
    PromptSuggestionEvent,
    SubAgentEvent,
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
        tool_metadata={},
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
    )
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
