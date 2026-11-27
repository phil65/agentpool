"""Protocol behaviour of the ACP server: errors, session loading, cancellation, usage."""

import asyncio
from decimal import Decimal
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock

from aclientp import RequestError
from aclientp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    CancelNotification,
    Cost,
    DeniedOutcome,
    ForkSessionRequest,
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
from pydantic_ai import ModelRequest, ModelResponse, RequestUsage, RunUsage, UsageLimitExceeded
from pydantic_ai.messages import TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest

from agentpool import Agent, AgentContext, AgentPool, AgentsManifest
from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import NoticeEvent, StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.messaging.messages import TokenCost
from agentpool.sessions import SessionData
from agentpool.tools.decisions import Approve, Deny
from agentpool_config.storage import SQLStorageConfig, StorageConfig
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.event_converter import ACPEventConverter
from agentpool_server.acp_server.input_provider import ACPInputProvider


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from aclientp.schema import SessionNotification
    from pydantic_ai import ModelMessage

    from agentpool.tools.decisions import ToolDecision


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


async def test_loading_restores_into_the_sessions_own_history(server: AgentPoolACPAgent) -> None:
    """The native agent runs sessions concurrently: a load leaves its own session alone."""
    agent = server.default_agent
    pool = agent.agent_pool
    assert pool is not None
    session_id = await _new_session(server)
    await server.prompt(_prompt(session_id))
    await server.session_manager.close_session(session_id)
    pool.sessions.remove(session_id)  # E.g. the server restarted

    await server.load_session(LoadSessionRequest(session_id=session_id, cwd="/tmp", mcp_servers=[]))

    restored = pool.sessions.get(session_id)
    assert restored is not None
    assert restored.history.get_history()  # Loaded into the session's own history
    assert not agent.conversation.get_history()  # Not into the agent's default session
    assert _texts(server, UserMessageChunk).count("hi") == 1  # Replayed from it


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


async def test_usage_limit_ends_the_turn_with_its_stop_reason(
    server: AgentPoolACPAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _new_session(server)
    session = server.session_manager.get_session(session_id)
    assert session is not None

    async def run_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise UsageLimitExceeded("The next request would exceed the request_limit of 3")
        yield

    monkeypatch.setattr(session.agent, "run_stream", run_stream)
    response = await server.prompt(_prompt(session_id))
    assert response.stop_reason == "max_turn_requests"


async def test_empty_prompt_is_refused(server: AgentPoolACPAgent) -> None:
    session_id = await _new_session(server)
    response = await server.prompt(PromptRequest(session_id=session_id, prompt=[]))
    assert response.stop_reason == "refusal"


async def test_prompt_of_only_commands_ends_without_running_the_agent(
    server: AgentPoolACPAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _new_session(server)
    session = server.session_manager.get_session(session_id)
    assert session is not None
    ran: list[object] = []

    async def run_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        ran.append(args)
        return
        yield

    monkeypatch.setattr(session.agent, "run_stream", run_stream)
    prompt = PromptRequest(session_id=session_id, prompt=[TextContentBlock(text="/help")])
    response = await server.prompt(prompt)
    assert response.stop_reason == "end_turn"
    assert ran == []


async def test_cancelled_permission_request_stops_the_turn() -> None:
    session = Mock()
    session.requests.request_permission = AsyncMock(
        return_value=RequestPermissionResponse(outcome=DeniedOutcome())
    )
    provider = ACPInputProvider(session)
    context = Mock(tool_name="bash", tool_input={"command": "ls"}, tool_call_id="tc1")
    assert await provider.get_tool_confirmation(context) == Deny(stop=True)


@pytest.mark.parametrize(
    ("option_id", "decision"),
    [
        ("allow_once", Approve()),
        ("allow_always", Approve(scope="session")),
        ("deny_once", Deny()),
        ("deny_always", Deny(scope="session")),
        ("something_else", Deny()),
    ],
)
async def test_selected_permission_options_are_decisions(
    option_id: str, decision: ToolDecision
) -> None:
    session = Mock()
    session.requests.request_permission = AsyncMock(
        return_value=RequestPermissionResponse(outcome=AllowedOutcome(option_id=option_id))
    )
    provider = ACPInputProvider(session)
    context = Mock(tool_name="bash", tool_input={"command": "ls"}, tool_call_id="tc1")
    assert await provider.get_tool_confirmation(context) == decision


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


async def test_background_events_are_sent_to_their_session(server: AgentPoolACPAgent) -> None:
    session_id = await _new_session(server)
    session = server.session_manager.get_session(session_id)
    assert session is not None
    agent = session.agent
    server.client.session_update.reset_mock()  # type: ignore[attr-defined]
    notice = NoticeEvent(title="Background task finished", description="3 files")
    background = BaseAgent.BackgroundEvent(
        agent_name=agent.name, session_id=session_id, event=notice
    )
    await agent.background_event.emit(background)
    other = BaseAgent.BackgroundEvent(agent_name=agent.name, session_id="other", event=notice)
    await agent.background_event.emit(other)
    calls = server.client.session_update.call_args_list  # type: ignore[attr-defined]
    notifications: list[SessionNotification] = [call.args[0] for call in calls]
    assert [n.session_id for n in notifications] == [session_id]
    assert "Background task finished" in str(notifications[0].update)


# --- rules (AGENTS.md / CLAUDE.md) --------------------------------------------


async def test_rules_go_into_the_first_prompt_even_when_slow_to_load() -> None:
    """The first turn waits for the rules; loading a session does not stage them again."""
    prompts: list[str] = []

    async def record(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        prompts.append(
            "".join(str(p.content) for p in request.parts if isinstance(p, UserPromptPart))
        )
        yield "ok"

    storage = StorageConfig(providers=[SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")])
    async with AgentPool(AgentsManifest(storage=storage)) as pool:
        agent = Agent(name="a", model=FunctionModel(stream_function=record), agent_pool=pool)
        pool.register("a", agent)
        loads: list[str | None] = []

        async def slow_rules(project_dir: str | None = None) -> str:
            loads.append(project_dir)
            await asyncio.sleep(0.05)
            return "RULES"

        agent.read_rules = slow_rules  # type: ignore[method-assign]
        server = AgentPoolACPAgent(client=AsyncMock(), default_agent=agent)
        await server.initialize(InitializeRequest(protocol_version=1))
        session_id = await _new_session(server)
        await server.prompt(_prompt(session_id, "first"))
        await server.prompt(_prompt(session_id, "second"))
        await server.session_manager.close_session(session_id)
        await server.load_session(LoadSessionRequest(session_id=session_id, cwd="/tmp"))
        await server.prompt(_prompt(session_id, "third"))
    assert loads == ["/tmp"], "rules are loaded once, for the new session"
    assert "first" in prompts[0]
    assert "RULES" in prompts[0]
    assert [p for p in prompts[1:] if "RULES" in p] == []


async def test_turns_on_a_shared_external_agent_are_serialized(server: AgentPoolACPAgent) -> None:
    """Agents with one native session per instance serialize turns; others need no lock."""
    from contextlib import nullcontext

    from agentpool.agents import AGUIAgent, ClaudeCodeAgent

    external = AGUIAgent(endpoint="http://agui.test", name="agui")
    assert not external.capabilities.concurrent_sessions
    lock = external.session_turn()
    assert isinstance(lock, asyncio.Lock)
    assert external.session_turn() is lock  # Every session holds the same one
    assert isinstance(server.default_agent.session_turn(), nullcontext)
    assert isinstance(ClaudeCodeAgent(name="claude").session_turn(), nullcontext)  # A process each


async def test_config_options_of_a_native_agent_are_per_acp_session(
    server: AgentPoolACPAgent,
) -> None:
    """Two ACP sessions share the pool's native agent; a mode set in one stays in it."""
    from aclientp.schema import SetSessionConfigOptionRequest

    first, second = await _new_session(server), await _new_session(server)
    request = SetSessionConfigOptionRequest(session_id=first, config_id="mode", value="always")
    await server.set_session_config_option(request)

    values: list[str] = []
    for session_id in (first, second):
        session = server.session_manager.get_session(session_id)
        assert session is not None
        mode = next(o for o in await session.config_options() if o.id == "mode")
        values.append(str(mode.current_value))
    assert values == ["always", "per_tool"]
    agent = server.default_agent
    assert isinstance(agent, Agent)
    assert agent.tool_confirmation_mode == "per_tool"  # The shared agent's own value


async def test_a_sessions_config_change_is_reported_to_its_own_client_only(
    server: AgentPoolACPAgent,
) -> None:
    """The change of one session's option is that session's: others don't hear of it."""
    from aclientp.schema import ConfigOptionUpdate, SetSessionConfigOptionRequest

    first, second = await _new_session(server), await _new_session(server)
    server.client.session_update.reset_mock()  # type: ignore[attr-defined]
    request = SetSessionConfigOptionRequest(session_id=first, config_id="mode", value="always")
    await server.set_session_config_option(request)

    calls = server.client.session_update.call_args_list  # type: ignore[attr-defined]
    updates = [
        call.args[0] for call in calls if isinstance(call.args[0].update, ConfigOptionUpdate)
    ]
    assert {n.session_id for n in updates} == {first}
    [update] = updates
    mode = next(o for o in update.update.config_options if o.id == "mode")
    assert mode.current_value == "always"  # As the session sees it, not the agent's own
    assert second not in {n.session_id for n in updates}


async def test_native_agents_see_the_instructions_of_their_session() -> None:
    """Each session's working directory reaches its own turns, not the other's."""
    seen: list[str] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        seen.append(request.instructions or "")
        yield "ok"

    storage = StorageConfig(providers=[SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")])
    async with AgentPool(AgentsManifest(storage=storage)) as pool:
        agent = Agent(name="a", model=FunctionModel(stream_function=stream), agent_pool=pool)
        pool.register("a", agent)
        async with agent:
            server = AgentPoolACPAgent(client=AsyncMock(), default_agent=agent)
            await server.initialize(InitializeRequest(protocol_version=1))
            first = await server.new_session(NewSessionRequest(cwd="/srv/one", mcp_servers=[]))
            second = await server.new_session(NewSessionRequest(cwd="/srv/two", mcp_servers=[]))
            await server.prompt(_prompt(first.session_id))
            await server.prompt(_prompt(second.session_id))
            await server.session_manager.close_all_sessions()
    assert "/srv/one" in seen[0]
    assert "/srv/two" not in seen[0]
    assert "/srv/two" in seen[1]
    assert "/srv/one" not in seen[1]


async def test_tools_run_in_the_environment_of_their_session(tmp_path: Path) -> None:
    """Sessions share the pool's agent; each turn's tools run where its session is."""
    from pydantic_ai import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, DeltaToolCalls

    ran_in: list[str | None] = []

    async def where(ctx: AgentContext) -> str:
        """Report the working directory."""
        ran_in.append(ctx.agent.env.cwd)
        return "ok"

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        if any(isinstance(part, ToolReturnPart) for part in request.parts):
            yield "done"
        else:
            yield {0: DeltaToolCall(name="where", json_args="{}")}

    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    async with AgentPool(AgentsManifest()) as pool:
        model = FunctionModel(stream_function=stream)
        agent = Agent(name="a", model=model, agent_pool=pool, tools=[where])
        pool.register("a", agent)
        async with agent:
            server = AgentPoolACPAgent(client=AsyncMock(), default_agent=agent)
            await server.initialize(InitializeRequest(protocol_version=1))
            first = await server.new_session(NewSessionRequest(cwd=str(one), mcp_servers=[]))
            second = await server.new_session(NewSessionRequest(cwd=str(two), mcp_servers=[]))
            await server.prompt(_prompt(first.session_id))
            await server.prompt(_prompt(second.session_id))
            own_cwd = agent.env.cwd
            await server.session_manager.close_all_sessions()
    assert ran_in == [str(one), str(two)]
    assert own_cwd not in (str(one), str(two))  # The shared agent keeps its own


async def test_acp_sessions_record_the_definition_version(server: AgentPoolACPAgent) -> None:
    """The server stores a session before its first turn: the version is added to it."""
    from agentpool.agents.definition import AgentDefinition
    from agentpool.models.agents import NativeAgentConfig

    agent = server.default_agent
    agent.definition = AgentDefinition.from_config(NativeAgentConfig(name="a", model="test"))
    session_id = await _new_session(server)
    await server.prompt(_prompt(session_id))
    stored = await server.session_manager.storage.load_session(session_id)
    assert stored is not None
    assert stored.definition_version == agent.definition.version
    assert stored.metadata.get("protocol") == "acp"  # The server's record is kept


async def test_fork_session_rejects_unissued_cursor_id(server: AgentPoolACPAgent) -> None:
    """Forking with a cursorId the server never issued must fail with -32602."""
    session_id = await _new_session(server)
    with pytest.raises(RequestError) as exc_info:
        await server.fork_session(
            ForkSessionRequest(
                session_id=session_id,
                cursor_id="unissued-cursor-id",
                cwd=".",
                mcp_servers=[],
            )
        )
    assert exc_info.value.code == INVALID_PARAMS


async def test_cancel_mid_stream_closes_generator_in_same_task(tmp_path: Path) -> None:
    """Cancelling while yielding must close the stream in the prompt task, not via GC."""
    first_chunk_sent = asyncio.Event()

    async def slow_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "chunk-1 "
        await asyncio.sleep(10)
        yield "chunk-2"

    async with AgentPool(AgentsManifest()) as pool:
        agent = Agent(name="a", model=FunctionModel(stream_function=slow_stream), agent_pool=pool)
        pool.register("a", agent)
        async with agent:
            client = AsyncMock()

            async def on_update(notification: SessionNotification) -> None:
                if isinstance(notification.update, AgentMessageChunk):
                    first_chunk_sent.set()
                    # Give session/cancel a chance to cancel while inside output.event()
                    await asyncio.sleep(0.05)

            client.session_update = AsyncMock(side_effect=on_update)
            server = AgentPoolACPAgent(client=client, default_agent=agent, load_skills=False)
            await server.initialize(InitializeRequest(protocol_version=1))
            sess = await server.new_session(NewSessionRequest(cwd=str(tmp_path), mcp_servers=[]))
            turn = asyncio.create_task(server.prompt(_prompt(sess.session_id)))
            await asyncio.wait_for(first_chunk_sent.wait(), 2)
            await server.cancel(CancelNotification(session_id=sess.session_id))
            response = await asyncio.wait_for(turn, 2)
            assert response.stop_reason == "cancelled"
            await server.session_manager.close_all_sessions()
