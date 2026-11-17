"""Tests for ACP session lifecycle features (delete, title, status, system prompt, notices)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

from pydantic_ai import FunctionToolCallEvent, ToolCallPart
import pytest

from acp.exceptions import RequestError
from acp.schema import (
    AgentMessageChunk,
    AuthStatusRequest,
    ClientCapabilities,
    ClientSessionCapabilities,
    CompactionCapabilities,
    CompactionUpdate,
    DeleteSessionRequest,
    InitializeRequest,
    NewSessionRequest,
    Notice,
    NoticeCapabilities,
    PromptSuggestionsUpdate,
    SessionInfoUpdate,
    SessionStatusRequest,
    SetSessionTitleRequest,
    SystemPrompt,
    TextContentBlock,
    ToolCallStart,
)
from agentpool.agents.events import (
    CompactionEvent,
    NoticeEvent,
    PromptSuggestionEvent,
    RunErrorEvent,
)
from agentpool_server.acp_server.event_converter import ACPEventConverter


if TYPE_CHECKING:
    from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


async def _collect(converter: ACPEventConverter, event: Any) -> list[Any]:
    return [u async for u in converter.convert(event)]


async def test_tool_call_start_carries_tool_name() -> None:
    event = FunctionToolCallEvent(part=ToolCallPart(tool_name="bash", args={}, tool_call_id="t1"))
    updates = await _collect(ACPEventConverter(), event)
    assert isinstance(updates[0], ToolCallStart)
    assert updates[0].name == "bash"


async def test_compaction_updates_only_when_supported() -> None:
    starting = CompactionEvent(session_id="s", phase="starting")
    completed = CompactionEvent(session_id="s", phase="completed")

    plain = await _collect(ACPEventConverter(), starting)
    assert not any(isinstance(u, CompactionUpdate) for u in plain)

    converter = ACPEventConverter(use_compaction_updates=True)
    first, second = [*await _collect(converter, starting), *await _collect(converter, completed)]
    assert isinstance(first, CompactionUpdate)
    assert isinstance(second, CompactionUpdate)
    assert (first.status, second.status) == ("in_progress", "completed")
    assert first.compaction_id == second.compaction_id


async def test_failed_compaction_carries_error() -> None:
    event = CompactionEvent(session_id="s", phase="failed", error="too large")
    [update] = await _collect(ACPEventConverter(use_compaction_updates=True), event)
    assert isinstance(update, CompactionUpdate)
    assert (update.status, update.error) == ("failed", "too large")

    [fallback] = await _collect(ACPEventConverter(), event)
    assert isinstance(fallback, AgentMessageChunk)


async def test_notice_events_fall_back_to_text() -> None:
    event = NoticeEvent(severity="warning", title="Low quota", description="10% left")
    [notice] = await _collect(ACPEventConverter(use_notices=True), event)
    assert notice == Notice(severity="warning", title="Low quota", description="10% left")

    [fallback] = await _collect(ACPEventConverter(), event)
    assert isinstance(fallback, AgentMessageChunk)
    assert "Low quota" in fallback.content.text


async def test_run_errors_become_notices_when_supported() -> None:
    event = RunErrorEvent(message="boom", agent_name="a")
    updates = await _collect(ACPEventConverter(use_notices=True), event)
    assert isinstance(updates[0], Notice)
    assert updates[0].severity == "error"
    assert updates[0].description == "boom"


async def test_prompt_suggestions_only_when_supported() -> None:
    event = PromptSuggestionEvent(suggestions=["run the tests"])
    assert await _collect(ACPEventConverter(), event) == []

    [update] = await _collect(ACPEventConverter(use_prompt_suggestions=True), event)
    assert isinstance(update, PromptSuggestionsUpdate)
    [suggestion] = update.suggestions
    assert suggestion.prompt == [TextContentBlock(text="run the tests")]


@pytest.fixture
async def initialized_agent(acp_agent: AgentPoolACPAgent) -> AgentPoolACPAgent:
    client: Any = acp_agent.client
    client.session_update = AsyncMock()
    session_caps = ClientSessionCapabilities(
        notices=NoticeCapabilities(), compaction=CompactionCapabilities()
    )
    caps = ClientCapabilities(session=session_caps)
    await acp_agent.initialize(InitializeRequest(protocol_version=1, client_capabilities=caps))
    return acp_agent


async def test_initialize_advertises_session_capabilities(acp_agent: AgentPoolACPAgent) -> None:
    response = await acp_agent.initialize(InitializeRequest(protocol_version=1))
    assert response.agent_capabilities
    caps = response.agent_capabilities.session_capabilities
    assert caps
    assert caps.delete is not None
    assert caps.system_prompt is not None
    assert caps.system_prompt.mode is None  # only `append`
    assert caps.additional_directories is not None
    assert caps.title is not None
    assert caps.status is not None
    assert response.agent_capabilities.auth
    assert response.agent_capabilities.auth.status is not None


async def test_auth_status_reports_default_agent(acp_agent: AgentPoolACPAgent) -> None:
    response = await acp_agent.auth_status(AuthStatusRequest())
    assert response.state == "not_required"
    assert response.accounts == []


async def test_session_lifecycle(initialized_agent: AgentPoolACPAgent) -> None:
    agent = initialized_agent
    system_prompt = SystemPrompt(mode="append", content=[TextContentBlock(text="Be terse.")])
    request = NewSessionRequest(
        cwd="/tmp/project",
        additional_directories=["/tmp/shared"],
        system_prompt=system_prompt,
    )
    session_id = (await agent.new_session(request)).session_id
    session = agent.session_manager.get_session(session_id)
    assert session
    assert session.supports_notices
    assert session.supports_compaction
    context = session.get_cwd_context()
    assert "/tmp/shared" in context
    assert "Be terse." in context

    status = await agent.session_status(SessionStatusRequest(session_id=session_id))
    assert status.status == "open"

    titled = await agent.set_session_title(SetSessionTitleRequest(session_id=session_id, title="X"))
    assert titled.title == "X"
    client: Any = agent.client
    sent = [c.args[0].update for c in client.session_update.call_args_list]
    assert any(isinstance(u, SessionInfoUpdate) and u.title == "X" for u in sent)

    await agent.delete_session(DeleteSessionRequest(session_id=session_id))
    assert agent.session_manager.get_session(session_id) is None
    with pytest.raises(RequestError):
        await agent.session_status(SessionStatusRequest(session_id=session_id))
