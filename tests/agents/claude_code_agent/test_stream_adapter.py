"""Tests for the Claude Code stream adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clawd_code_sdk.models import PromptSuggestionMessage, StatusSystemMessage

from agentpool.agents.claude_code_agent.stream_adapter import ClaudeCodeStreamedResponse
from agentpool.agents.events import CompactionEvent, PromptSuggestionEvent


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
