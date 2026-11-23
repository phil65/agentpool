"""The ACP v2 agent over a real v2 connection (in-memory streams, wire-validated)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from aclientp.schema import v2
from aclientp.v2.agent import AgentSideConnection
from aclientp.v2.client import Client, ClientSideConnection
import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream
from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest

from agentpool import Agent, AgentPool, AgentsManifest
from agentpool_config.storage import SQLStorageConfig, StorageConfig
from agentpool_server.acp_server.v2_agent import AgentPoolV2Agent


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
    from pydantic_ai import ModelMessage


TIMEOUT = 5


class _Reader(ByteReceiveStream):
    def __init__(self, stream: MemoryObjectReceiveStream[bytes]) -> None:
        self._stream = stream

    async def receive(self, max_bytes: int = 65536) -> bytes:
        try:
            return await self._stream.receive()
        except anyio.ClosedResourceError as exc:
            raise anyio.EndOfStream from exc

    async def aclose(self) -> None:
        await self._stream.aclose()


class _Writer(ByteSendStream):
    def __init__(self, stream: MemoryObjectSendStream[bytes]) -> None:
        self._stream = stream

    async def send(self, item: bytes) -> None:
        await self._stream.send(item)

    async def aclose(self) -> None:
        await self._stream.aclose()


class RecordingClient(Client):
    """A v2 client that records session updates and allows every permission request."""

    def __init__(self) -> None:
        self.updates: list[v2.SessionUpdate] = []
        self.idle = asyncio.Event()
        self.permission_requests: list[v2.RequestPermissionRequest] = []

    async def session_update(self, params: v2.UpdateSessionNotification) -> None:
        self.updates.append(params.update)
        if isinstance(params.update, v2.IdleStateSessionUpdate):
            self.idle.set()

    async def request_permission(
        self, params: v2.RequestPermissionRequest
    ) -> v2.RequestPermissionResponse:
        self.permission_requests.append(params)
        option = params.options[0]
        return v2.RequestPermissionResponse(
            outcome=v2.SelectedPermissionOutcome(option_id=option.option_id)
        )

    def of[T](self, kind: type[T]) -> list[T]:
        return [u for u in self.updates if isinstance(u, kind)]

    async def wait_idle(self) -> v2.IdleStateSessionUpdate:
        await asyncio.wait_for(self.idle.wait(), TIMEOUT)
        self.idle.clear()
        return self.of(v2.IdleStateSessionUpdate)[-1]


async def _reply(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    yield "Hello"
    yield " there"


@pytest.fixture
async def connected(
    tmp_path: Any,
) -> AsyncIterator[tuple[ClientSideConnection, RecordingClient, Agent[None, str]]]:
    storage = StorageConfig(providers=[SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")])
    async with AgentPool(AgentsManifest(storage=storage)) as pool:
        agent = Agent(name="a", model=FunctionModel(stream_function=_reply), agent_pool=pool)
        pool.register("a", agent)
        to_agent_send, to_agent_recv = anyio.create_memory_object_stream[bytes](100)
        to_client_send, to_client_recv = anyio.create_memory_object_stream[bytes](100)
        agent_conn = AgentSideConnection(
            lambda conn: AgentPoolV2Agent(conn, default_agent=agent),
            _Writer(to_client_send),
            _Reader(to_agent_recv),
        )
        recorder = RecordingClient()
        client_conn = ClientSideConnection(
            recorder, _Writer(to_agent_send), _Reader(to_client_recv)
        )
        yield client_conn, recorder, agent
        await client_conn.close()
        await agent_conn.close()


async def _initialize(conn: ClientSideConnection) -> v2.InitializeResponse:
    request = v2.InitializeRequest(
        protocol_version=2, info=v2.Implementation(name="test-client", version="1")
    )
    return await conn.initialize(request)


async def _new_session(conn: ClientSideConnection, cwd: str) -> str:
    await _initialize(conn)
    response = await conn.new_session(v2.NewSessionRequest(cwd=cwd))
    return response.session_id


def _prompt(session_id: str, text: str = "hi") -> v2.PromptRequest:
    return v2.PromptRequest(session_id=session_id, prompt=[v2.TextContent(text=text)])


async def test_initialize_answers_v2(connected: Any) -> None:
    conn, _, _ = connected
    response = await _initialize(conn)
    assert response.protocol_version == 2
    assert response.info.name == "agentpool"
    assert isinstance(response.capabilities, v2.AgentCapabilities)
    session = response.capabilities.session
    assert isinstance(session, v2.SessionCapabilities)
    assert isinstance(session.mcp, v2.McpCapabilities)
    assert v2.is_set(session.mcp.stdio)


async def test_prompt_runs_as_a_v2_turn(connected: Any, tmp_path: Any) -> None:
    conn, recorder, _ = connected
    session_id = await _new_session(conn, str(tmp_path))

    accepted = await conn.prompt(_prompt(session_id))
    idle = await recorder.wait_idle()

    assert accepted.message_id
    assert idle.stop_reason == "end_turn"
    states = [u.state for u in recorder.updates if isinstance(u, v2.RunningStateSessionUpdate)]
    assert states == ["running"]
    chunks = recorder.of(v2.AgentMessageChunk)
    text = "".join(c.content.text for c in chunks if isinstance(c.content, v2.TextContent))
    assert text == "Hello there"
    assert len({c.message_id for c in chunks}) == 1  # One message, one id


async def test_agent_failure_ends_the_turn_with_an_error(
    connected: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v1 can only report it as a notice; v2 has stopReason: "error"."""
    conn, recorder, agent = connected
    session_id = await _new_session(conn, str(tmp_path))

    async def run_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise RuntimeError("model exploded")
        yield

    monkeypatch.setattr(agent, "run_stream", run_stream)
    await conn.prompt(_prompt(session_id))
    idle = await recorder.wait_idle()

    assert idle.stop_reason == "error"
    assert isinstance(idle.error, v2.Error)
    assert "model exploded" in idle.error.message
    assert any("model exploded" in n.title for n in recorder.of(v2.Notice))


async def test_cancel_ends_the_turn_as_cancelled(
    connected: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, recorder, agent = connected
    session_id = await _new_session(conn, str(tmp_path))
    started = asyncio.Event()
    interrupted = asyncio.Event()

    async def run_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        started.set()
        await interrupted.wait()
        return
        yield

    async def interrupt(session_id: str | None = None) -> None:
        assert session_id == session_id_of_turn  # Only this session's turn
        interrupted.set()

    session_id_of_turn = session_id
    monkeypatch.setattr(agent, "run_stream", run_stream)
    monkeypatch.setattr(agent, "interrupt", interrupt)
    await conn.prompt(_prompt(session_id))
    await asyncio.wait_for(started.wait(), TIMEOUT)
    await conn.cancel(v2.CancelSessionNotification(session_id=session_id))
    idle = await recorder.wait_idle()

    assert idle.stop_reason == "cancelled"


async def test_commands_and_config_options_arrive_in_v2_shape(
    connected: Any, tmp_path: Any
) -> None:
    conn, recorder, _ = connected
    await _new_session(conn, str(tmp_path))

    async def commands_sent() -> None:
        while not recorder.of(v2.AvailableCommandsUpdate):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(commands_sent(), TIMEOUT)
    [update] = recorder.of(v2.AvailableCommandsUpdate)
    assert all(isinstance(c, v2.Command) for c in update.available_commands)
    assert "help" in {c.name for c in update.available_commands if isinstance(c, v2.Command)}


async def test_list_and_resume_with_replay(connected: Any, tmp_path: Any) -> None:
    conn, recorder, _ = connected
    session_id = await _new_session(conn, str(tmp_path))
    await conn.prompt(_prompt(session_id, "remember this"))
    await recorder.wait_idle()

    listed = await conn.list_sessions(v2.ListSessionsRequest())
    assert session_id in {s.session_id for s in listed.sessions}

    await conn.close_session(v2.CloseSessionRequest(session_id=session_id))
    recorder.updates.clear()
    await conn.resume_session(
        v2.ResumeSessionRequest(
            session_id=session_id, cwd=str(tmp_path), replay_from=v2.ReplayFromStart()
        )
    )

    # The history comes first, then replay_complete (commands may follow later)
    done = next(i for i, u in enumerate(recorder.updates) if isinstance(u, v2.ReplayComplete))
    replayed = "".join(
        u.content.text
        for u in recorder.updates[:done]
        if isinstance(u, v2.UserMessageChunk) and isinstance(u.content, v2.TextContent)
    )
    assert "remember this" in replayed


def test_permission_request_and_answers_in_v2() -> None:
    from aclientp.schema import (
        AllowedOutcome,
        DeniedOutcome,
        PermissionOption,
        RequestPermissionRequest,
        ToolCallUpdate,
    )

    from agentpool_server.acp_server.v2_client import permission_request, permission_response

    request = RequestPermissionRequest(
        session_id="s",
        tool_call=ToolCallUpdate(tool_call_id="t1", title="Run rm -rf build", kind="execute"),
        options=[
            PermissionOption(option_id="allow", name="Allow", kind="allow_once"),
            PermissionOption(option_id="deny", name="Deny", kind="reject_once"),
        ],
    )
    converted = permission_request(request)
    wire = v2.to_wire(converted)
    v2.RequestPermissionRequest.model_validate(wire)  # What a v2 client accepts
    assert wire["title"] == "Run rm -rf build"
    assert wire["subject"] == {
        "type": "tool_call",
        "toolCall": {"toolCallId": "t1", "title": "Run rm -rf build", "kind": "execute"},
    }
    assert [o["kind"] for o in wire["options"]] == ["allow_once", "reject_once"]

    selected = v2.RequestPermissionResponse(outcome=v2.SelectedPermissionOutcome(option_id="allow"))
    cancelled = v2.RequestPermissionResponse(outcome=v2.CancelledPermissionOutcome())
    assert permission_response(selected).outcome == AllowedOutcome(option_id="allow")
    assert isinstance(permission_response(cancelled).outcome, DeniedOutcome)


async def test_mid_turn_prompt_steers_without_duplicate_running_state(
    connected: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second v2 session/prompt during an active turn steers without a second running state."""
    conn, recorder, agent = connected
    session_id = await _new_session(conn, str(tmp_path))
    steered_session = session_id
    started = asyncio.Event()
    release = asyncio.Event()
    steered: list[str] = []

    async def run_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        started.set()
        await release.wait()
        return
        yield

    async def steer(
        message: str, *, interrupt: bool = False, session_id: str | None = None
    ) -> None:
        assert session_id == steered_session  # Into this session's turn
        steered.append(message)
        release.set()

    monkeypatch.setattr(agent, "run_stream", run_stream)
    monkeypatch.setattr(agent, "steer", steer)

    first = await conn.prompt(_prompt(session_id, "start"))
    await asyncio.wait_for(started.wait(), TIMEOUT)
    second = await conn.prompt(_prompt(session_id, "actually do X"))
    idle = await recorder.wait_idle()

    assert first.message_id != second.message_id
    assert steered == ["actually do X"]
    assert idle.stop_reason == "end_turn"
    running_states = [u for u in recorder.updates if isinstance(u, v2.RunningStateSessionUpdate)]
    assert len(running_states) == 1


async def test_two_v2_sessions_isolate_native_agent_history(
    connected: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two ACP v2 sessions on the same native pool agent keep isolated Session histories."""
    # Global rules come from the user's config dir: provide them here instead of relying
    # on the machine's ~/.config/agentpool
    global_dir = tmp_path / "global_config"
    global_dir.mkdir()
    (global_dir / "AGENTS.md").write_text("Be terse.", encoding="utf-8")
    monkeypatch.setattr("agentpool_config.resolution.get_global_config_dir", lambda: global_dir)
    conn, recorder, agent = connected
    sid_a = await _new_session(conn, str(tmp_path))
    sid_b = (await conn.new_session(v2.NewSessionRequest(cwd=str(tmp_path)))).session_id

    await conn.prompt(_prompt(sid_a, "alpha prompt"))
    await recorder.wait_idle()
    recorder.updates.clear()

    await conn.prompt(_prompt(sid_b, "beta prompt"))
    await recorder.wait_idle()

    pool = agent.agent_pool
    assert pool is not None
    sess_a = pool.sessions.get(sid_a)
    sess_b = pool.sessions.get(sid_b)
    assert sess_a is not None
    assert sess_b is not None
    # Each session's first prompt is its own and carries its own rules once (global
    # rules set up above); before, session A's turn took both sessions' rules
    for session, prompt in ((sess_a, "alpha prompt"), (sess_b, "beta prompt")):
        first = session.history.get_history()[0].content
        assert isinstance(first, list)
        assert first[0] == prompt
        assert sum(str(part).count("Global Rules") for part in first) == 1
    # The shared native agent's default conversation was never polluted
    assert agent.conversation.get_history() == []


class _WaitingConnection:
    """The part of a v2 connection a permission request uses: answers once released."""

    def __init__(self) -> None:
        self.states: list[tuple[str, str]] = []
        self.release = asyncio.Event()

    async def session_update(self, params: v2.UpdateSessionNotification) -> None:
        match params.update:
            case v2.RunningStateSessionUpdate() | v2.RequiresActionStateSessionUpdate() as update:
                self.states.append((params.session_id, update.state))

    async def request_permission(
        self, params: v2.RequestPermissionRequest
    ) -> v2.RequestPermissionResponse:
        await self.release.wait()
        option = params.options[0]
        return v2.RequestPermissionResponse(
            outcome=v2.SelectedPermissionOutcome(option_id=option.option_id)
        )


async def test_permission_requests_report_requires_action() -> None:
    """The session requires_action while its requests wait, running once none does."""
    from aclientp.schema import PermissionOption, RequestPermissionRequest, ToolCallUpdate

    from agentpool_server.acp_server.v2_client import V2ClientAdapter
    from agentpool_server.acp_server.v2_translate import UpdateTranslator

    def request(tool_call_id: str) -> RequestPermissionRequest:
        return RequestPermissionRequest(
            session_id="s",
            tool_call=ToolCallUpdate(tool_call_id=tool_call_id, title="Run it"),
            options=[PermissionOption(option_id="allow", name="Allow", kind="allow_once")],
        )

    conn = _WaitingConnection()
    adapter = V2ClientAdapter(conn, UpdateTranslator(message_id="m"))  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    both = asyncio.gather(
        adapter.request_permission(request("t1")), adapter.request_permission(request("t2"))
    )
    await asyncio.sleep(0.05)
    assert conn.states == [("s", "requires_action")]  # Once, for both waiting requests
    conn.release.set()
    await asyncio.wait_for(both, TIMEOUT)
    assert conn.states == [("s", "requires_action"), ("s", "running")]
