"""Tests for converting ACP session updates in the ACP agent stream adapter."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from acp.schema import (
    CompactionUpdate,
    ImageContentBlock,
    InitializeRequest,
    Notice,
    PromptSuggestion,
    PromptSuggestionsUpdate,
    ResourceContentBlock,
    TextContentBlock,
)
from agentpool.agents.acp_agent.client_handler import TimeoutableEvent
from agentpool.agents.acp_agent.session_state import ACPSessionState
from agentpool.agents.acp_agent.stream_adapter import AcpAgentStreamedResponse
from agentpool.agents.events import CompactionEvent, NoticeEvent, PromptSuggestionEvent


if TYPE_CHECKING:
    from acp.schema import SessionUpdate
    from agentpool.agents.events import RichAgentStreamEvent


async def _stream(*updates: SessionUpdate) -> list[RichAgentStreamEvent[str]]:
    state = ACPSessionState(session_id="remote")
    for update in updates:
        state.add_update(update)
    prompt_task = asyncio.create_task(asyncio.sleep(0))
    await prompt_task
    response = AcpAgentStreamedResponse(
        provider_name="acp",
        state=state,
        update_event=TimeoutableEvent(),
        prompt_task=prompt_task,
        agent_name="agent",
        session_id="local",
        tool_metadata={},
    )
    return [event async for event in response._get_event_iterator()]


async def test_prompt_suggestions_inline_resource_links() -> None:
    link = ResourceContentBlock(uri="file:///a.py", name="a.py")
    image = ImageContentBlock(data="", mime_type="image/png")
    update = PromptSuggestionsUpdate(
        suggestions=[
            PromptSuggestion(prompt=[TextContentBlock(text="fix "), link]),
            PromptSuggestion(prompt=[image]),
        ]
    )
    assert await _stream(update) == [
        PromptSuggestionEvent(suggestions=["fix [a.py](file:///a.py)"])
    ]


async def test_compaction_updates_start_once_per_id() -> None:
    events = await _stream(
        CompactionUpdate(compaction_id="c1", status="in_progress"),
        CompactionUpdate(compaction_id="c1", status="in_progress"),
        CompactionUpdate(compaction_id="c1", status="completed"),
        CompactionUpdate(compaction_id="c2", status="failed", error="too large"),
        CompactionUpdate(compaction_id="c3", status="cancelled"),
    )
    assert events == [
        CompactionEvent(session_id="local", phase="starting"),
        CompactionEvent(session_id="local", phase="completed"),
        CompactionEvent(session_id="local", phase="failed", error="too large"),
        CompactionEvent(session_id="local", phase="failed"),
    ]


async def test_notices_map_unknown_severity_to_info() -> None:
    events = await _stream(
        Notice(severity="warning", title="Low quota", description="10% left"),
        Notice(severity="critical", title="Custom"),
    )
    assert events == [
        NoticeEvent(severity="warning", title="Low quota", description="10% left"),
        NoticeEvent(severity="info", title="Custom"),
    ]


def test_initialize_request_advertises_session_capabilities() -> None:
    request = InitializeRequest.create_for_package(
        "agentpool", prompt_suggestions=True, compaction=True, notices=True
    )
    assert request.client_capabilities
    session = request.client_capabilities.session
    assert session
    assert session.prompt_suggestions is not None
    assert session.compaction is not None
    assert session.notices is not None
