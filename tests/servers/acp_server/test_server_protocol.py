"""Protocol behaviour of the ACP server: errors, session loading, cancellation, usage."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock

from aclientp import RequestError
from aclientp.schema import (
    AgentMessageChunk,
    CancelNotification,
    Cost,
    DeniedOutcome,
    InitializeRequest,
    LoadSessionRequest,
    NewSessionRequest,
    PromptRequest,
    RequestPermissionResponse,
    ResumeSessionRequest,
    SetSessionConfigOptionRequest,
    SetSessionModeRequest,
    TextContentBlock,
    UsageUpdate,
    UserMessageChunk,
)
from pydantic_ai import ModelRequest, ModelResponse, RequestUsage, RunUsage
from pydantic_ai.messages import TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest

from agentpool import Agent, AgentPool, AgentsManifest
from agentpool.agents.events import StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.messaging.messages import TokenCost
from agentpool.sessions import SessionData
from agentpool_config.storage import SQLStorageConfig, StorageConfig
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.event_converter import ACPEventConverter
from agentpool_server.acp_server.input_provider import ACPInputProvider


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from aclientp.schema import SessionNotification
    from pydantic_ai import ModelMessage


RESOURCE_NOT_FOUND = -32002
INVALID_PARAMS = -32602
METHOD_NOT_FOUND = -32601


async def _stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    for _ in range(300):
        yield "x"


@pytest.fixture
async def server() -> AsyncIterator[AgentPoolACPAgent]:
    storage = StorageConfig(providers=[SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")])
    async with AgentPool(AgentsManifest(storage=storage)) as pool:
        agent = Agent(name="a", model=FunctionModel(stream_function=_stream), agent_pool=pool)
        pool.register("a", agent)
        client = AsyncMock()
        client.read_text_file.side_effect = RequestError.resource_not_found()
        acp_agent = AgentPoolACPAgent(client=client, default_agent=agent)
        await acp_agent.initialize(InitializeRequest(protocol_version=1))
        yield acp_agent
        await acp_agent.session_manager.close_all_sessions()


def _texts[T: AgentMessageChunk | UserMessageChunk](
    server: AgentPoolACPAgent, chunk_type: type[T]
) -> list[str]:
    """Texts of the message chunks of a kind sent to the client."""
    calls = server.client.session_update.call_args_list  # type: ignore[attr-defined]
    notifications: list[SessionNotification] = [call.args[0] for call in calls]
    return [
        n.update.content.text
        for n in notifications
        if isinstance(n.update, chunk_type) and isinstance(n.update.content, TextContentBlock)
    ]


async def _new_session(server: AgentPoolACPAgent) -> str:
    response = await server.new_session(NewSessionRequest(cwd="/tmp", mcp_servers=[]))
    return response.session_id


def _prompt(session_id: str, text: str = "hi") -> PromptRequest:
    return PromptRequest(session_id=session_id, prompt=[TextContentBlock(text=text)])


# --- errors ------------------------------------------------------------------


async def test_requests_before_initialize_are_rejected() -> None:
    acp_agent = AgentPoolACPAgent(client=Mock(), default_agent=Mock())
    with pytest.raises(RequestError, match="Invalid request"):
        await acp_agent.new_session(NewSessionRequest(cwd="/tmp", mcp_servers=[]))


async def test_unknown_extension_method_is_method_not_found(server: AgentPoolACPAgent) -> None:
    with pytest.raises(RequestError) as exc_info:
        await server.ext_method("vendor/thing", {})
    assert exc_info.value.code == METHOD_NOT_FOUND
    assert exc_info.value.data == {"method": "_vendor/thing"}


async def test_set_mode_on_unknown_session(server: AgentPoolACPAgent) -> None:
    with pytest.raises(RequestError) as exc_info:
        await server.set_session_mode(SetSessionModeRequest(session_id="nope", mode_id="never"))
    assert exc_info.value.code == RESOURCE_NOT_FOUND


async def test_set_unknown_mode_is_invalid_params(server: AgentPoolACPAgent) -> None:
    session_id = await _new_session(server)
    with pytest.raises(RequestError) as exc_info:
        await server.set_session_mode(SetSessionModeRequest(session_id=session_id, mode_id="nope"))
    assert exc_info.value.code == INVALID_PARAMS


async def test_set_unknown_config_option_is_invalid_params(server: AgentPoolACPAgent) -> None:
    session_id = await _new_session(server)
    request = SetSessionConfigOptionRequest(session_id=session_id, config_id="nope", value="x")
    with pytest.raises(RequestError) as exc_info:
        await server.set_session_config_option(request)
    assert exc_info.value.code == INVALID_PARAMS


# --- loading sessions --------------------------------------------------------


async def _attach(
    server: AgentPoolACPAgent, request: LoadSessionRequest | ResumeSessionRequest
) -> None:
    match request:
        case LoadSessionRequest():
            await server.load_session(request)
        case ResumeSessionRequest():
            await server.resume_session(request)


@pytest.mark.parametrize("request_type", [LoadSessionRequest, ResumeSessionRequest])
async def test_loading_unknown_session_fails_without_storing_it(
    server: AgentPoolACPAgent, request_type: type[LoadSessionRequest | ResumeSessionRequest]
) -> None:
    request = request_type(session_id="does-not-exist", cwd="/tmp", mcp_servers=[])
    with pytest.raises(RequestError) as exc_info:
        await _attach(server, request)
    assert exc_info.value.code == RESOURCE_NOT_FOUND
    assert await server.session_manager.storage.load_session("does-not-exist") is None
    assert server.session_manager.get_session("does-not-exist") is None


async def test_loading_keeps_stored_session_record(server: AgentPoolACPAgent) -> None:
    storage = server.session_manager.storage
    session_id = await _new_session(server)
    await server.prompt(_prompt(session_id))
    await storage.update_session_title(session_id, "My title")
    stored = await storage.load_session(session_id)
    assert stored is not None
    await storage.save_session(stored.model_copy(update={"sdk_session_id": "sdk-1"}))
    await server.session_manager.close_session(session_id)  # E.g. the editor restarted

    request = LoadSessionRequest(session_id=session_id, cwd="/tmp", mcp_servers=[])
    await server.load_session(request)

    loaded = await storage.load_session(session_id)
    assert loaded is not None
    assert loaded.title == "My title"
    assert loaded.sdk_session_id == "sdk-1"
    assert loaded.created_at == stored.created_at
    # The history was replayed, the user prompt once
    assert _texts(server, UserMessageChunk).count("hi") == 1


async def test_prompt_for_stored_inactive_session_recreates_it(server: AgentPoolACPAgent) -> None:
    await server.session_manager.storage.save_session(
        SessionData(session_id="stored", agent_name="a", cwd="/tmp")
    )
    response = await server.prompt(_prompt("stored"))
    assert response.stop_reason == "end_turn"


async def test_prompt_for_unknown_session_fails(server: AgentPoolACPAgent) -> None:
    with pytest.raises(RequestError) as exc_info:
        await server.prompt(_prompt("does-not-exist"))
    assert exc_info.value.code == RESOURCE_NOT_FOUND


# --- prompt turns ------------------------------------------------------------


async def test_streaming_is_not_throttled(server: AgentPoolACPAgent) -> None:
    session_id = await _new_session(server)
    start = time.perf_counter()
    await server.prompt(_prompt(session_id))
    # 300 text events; a per-event sleep of 10 ms alone took 3 s
    assert time.perf_counter() - start < 1.5


async def test_cancel_reports_cancelled_when_stream_ends_quietly(
    server: AgentPoolACPAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _new_session(server)
    session = server.session_manager.get_session(session_id)
    assert session is not None
    started = asyncio.Event()
    interrupted = asyncio.Event()

    async def run_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        started.set()
        await interrupted.wait()
        return  # Like agents that end their stream without a final event
        yield

    async def interrupt() -> None:
        interrupted.set()

    monkeypatch.setattr(session.agent, "run_stream", run_stream)
    monkeypatch.setattr(session.agent, "interrupt", interrupt)
    turn = asyncio.create_task(server.prompt(_prompt(session_id)))
    await asyncio.wait_for(started.wait(), 2)
    await server.cancel(CancelNotification(session_id=session_id))
    response = await asyncio.wait_for(turn, 2)
    assert response.stop_reason == "cancelled"


async def test_agent_error_is_reported_within_the_turn(
    server: AgentPoolACPAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _new_session(server)
    session = server.session_manager.get_session(session_id)
    assert session is not None

    async def run_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise RuntimeError("model exploded")
        yield

    monkeypatch.setattr(session.agent, "run_stream", run_stream)
    response = await server.prompt(_prompt(session_id))
    assert response.stop_reason == "end_turn"
    # Already sent when the response is returned, not scheduled for later
    assert any("model exploded" in text for text in _texts(server, AgentMessageChunk))


async def test_cancelled_permission_request_aborts_the_run() -> None:
    session = Mock()
    session.requests.request_permission = AsyncMock(
        return_value=RequestPermissionResponse(outcome=DeniedOutcome())
    )
    provider = ACPInputProvider(session)
    context = Mock(tool_name="bash", tool_input={"command": "ls"}, tool_call_id="tc1")
    assert await provider.get_tool_confirmation(context) == "abort_run"


# --- usage -------------------------------------------------------------------


def _completed_run(cost: str | None) -> StreamCompleteEvent[Any]:
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="q")]),
        ModelResponse(parts=[TextPart(content="t")], usage=RequestUsage(input_tokens=100)),
        ModelRequest(parts=[UserPromptPart(content="tool result")]),
        ModelResponse(
            parts=[TextPart(content="a")], usage=RequestUsage(input_tokens=150, output_tokens=20)
        ),
    ]
    message = ChatMessage(
        content="a",
        role="assistant",
        messages=messages,
        usage=RunUsage(requests=2, input_tokens=250, output_tokens=20),
        cost_info=TokenCost(total_cost=Decimal(cost)) if cost else None,
    )
    return StreamCompleteEvent(message=message)


async def test_usage_update_reports_context_fill_and_session_cost() -> None:
    converter = ACPEventConverter(context_window=1000, session_cost=Decimal("0.5"))
    match [u async for u in converter.convert(_completed_run("0.25"))]:
        case [UsageUpdate(used=used, size=size, cost=Cost(amount=amount))]:
            assert used == 170  # The last request only, not the summed run usage
            assert size == 1000
            assert amount == 0.75  # Cumulative over the session
        case other:
            pytest.fail(f"Expected one usage update, got {other}")
    assert converter.session_cost == Decimal("0.75")
    assert converter.last_usage is not None
    assert converter.last_usage.total_tokens == 270


async def test_no_usage_update_without_known_context_window() -> None:
    converter = ACPEventConverter()
    updates = [u async for u in converter.convert(_completed_run("0.25"))]
    assert updates == []
    assert converter.session_cost == Decimal("0.25")
