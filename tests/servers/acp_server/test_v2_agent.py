"""The ACP v2 agent over a real v2 connection (in-memory streams, wire-validated)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
import sys
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from aclientp import RequestError
from aclientp.schema import v2
from aclientp.v2.agent import AgentSideConnection
from aclientp.v2.client import Client, ClientSideConnection
import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
import pytest

from agentpool import Agent, AgentContext, AgentPool, AgentsManifest
from agentpool_config.storage import SQLStorageConfig, StorageConfig
from agentpool_server.acp_server import session as acp_session
from agentpool_server.acp_server.v2_agent import AgentPoolV2Agent


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping, Sequence

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
    from pydantic_ai import ModelMessage


TIMEOUT = 5
WAIT_TIMEOUT = 0x102
SYNCHRONIZE_AND_QUERY = 0x00100000 | 0x1000


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
        self.notifications: list[v2.UpdateSessionNotification] = []
        self.idle = asyncio.Event()
        self.permission_requests: list[v2.RequestPermissionRequest] = []

    async def session_update(self, params: v2.UpdateSessionNotification) -> None:
        self.updates.append(params.update)
        self.notifications.append(params)
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
    async with _serve(FunctionModel(stream_function=_reply)) as served:
        yield served


@asynccontextmanager
async def _serve(
    model: FunctionModel,
    tools: Sequence[Callable[..., Any]] = (),
    *,
    debug_commands: bool = False,
    storage_url: str = "sqlite+aiosqlite:///:memory:",
    toolsets: Sequence[Any] = (),
    helpers: Mapping[str, Any] = MappingProxyType({}),
) -> AsyncGenerator[tuple[ClientSideConnection, RecordingClient, Agent[None, str]]]:
    """A v2 connection to an agent with ``model`` (and ``tools``) in a pool.

    ``helpers``: more agents of the pool, by name, with their model (e.g. subagents).
    """
    storage = StorageConfig(providers=[SQLStorageConfig(url=storage_url)])
    async with AgentPool(AgentsManifest(storage=storage)) as pool:
        for name, helper_model in helpers.items():
            await pool.add_agent(Agent(name=name, model=helper_model))
        agent = Agent(
            name="a", model=model, tools=list(tools), toolsets=list(toolsets), agent_pool=pool
        )
        pool.register("a", agent)
        to_agent_send, to_agent_recv = anyio.create_memory_object_stream[bytes](100)
        to_client_send, to_client_recv = anyio.create_memory_object_stream[bytes](100)
        agent_conn = AgentSideConnection(
            lambda conn: AgentPoolV2Agent(conn, default_agent=agent, debug_commands=debug_commands),
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


async def test_cancel_during_a_tool_call_ends_the_turn(tmp_path: Any) -> None:
    """A real native run cancelled while a tool runs still ends with idle/cancelled."""
    tool_started = asyncio.Event()

    async def slow_tool() -> str:
        """Take a long time."""
        tool_started.set()
        await asyncio.sleep(30)
        return "done"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="slow_tool", json_args="{}", tool_call_id="c1")}
        else:
            yield "finished"

    async with _serve(FunctionModel(stream_function=stream), [slow_tool]) as (conn, recorder, _):
        session_id = await _new_session(conn, str(tmp_path))
        await conn.prompt(_prompt(session_id))
        await asyncio.wait_for(tool_started.wait(), TIMEOUT)
        await conn.cancel(v2.CancelSessionNotification(session_id=session_id))
        idle = await recorder.wait_idle()

        assert idle.stop_reason == "cancelled"
        statuses = [u.status for u in recorder.of(v2.ToolCallSessionUpdate)]
        assert statuses[-1] == "cancelled"  # v2 has the status; not "completed"
        assert "completed" not in statuses
        # The session takes the next prompt
        await conn.prompt(_prompt(session_id))
        assert (await recorder.wait_idle()).stop_reason == "end_turn"


def _process_alive(pid: int) -> bool:
    """Whether a process runs (``os.kill(pid, 0)`` would kill it on Windows)."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE_AND_QUERY, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_cancelling_a_command_kills_its_process(tmp_path: Any) -> None:
    """The bash tool's process (and what it started) does not outlive a cancelled turn."""
    from exxec import LocalExecutionEnvironment

    from agentpool.tool_impls.bash import create_bash_tool

    pid_file = tmp_path / "pid"
    script = tmp_path / "sleeper.py"
    script.write_text(
        "import os, pathlib, sys, time\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    command = f'"{sys.executable}" "{script}" "{pid_file}"'

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if len(messages) == 1:
            args = json.dumps({"command": command})
            yield {0: DeltaToolCall(name="bash", json_args=args, tool_call_id="c1")}
        else:
            yield "done"

    env = LocalExecutionEnvironment(default_command_timeout=None)
    bash = create_bash_tool(env=env)
    async with _serve(FunctionModel(stream_function=stream), [bash]) as (conn, recorder, _):
        session_id = await _new_session(conn, str(tmp_path))
        await conn.prompt(_prompt(session_id))
        async with asyncio.timeout(TIMEOUT * 2):
            while not pid_file.exists() or not pid_file.read_text():
                await asyncio.sleep(0.05)
        pid = int(pid_file.read_text())
        assert _process_alive(pid)
        await conn.cancel(v2.CancelSessionNotification(session_id=session_id))
        assert (await recorder.wait_idle()).stop_reason == "cancelled"

    async with asyncio.timeout(TIMEOUT):
        while _process_alive(pid):
            await asyncio.sleep(0.1)


async def test_processes_show_as_v2_terminals(tmp_path: Any) -> None:
    """A tool's process reaches the client as a terminal with its command, output and exit."""

    async def run_command(ctx: AgentContext) -> str:
        """Run a command."""
        await ctx.events.process_started("p1", "echo hi")
        await ctx.events.process_output("p1", "hi\n")
        await ctx.events.process_exit("p1", 0, final_output="hi\n")
        return "hi"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="run_command", json_args="{}", tool_call_id="c1")}
        else:
            yield "ran it"

    model = FunctionModel(stream_function=stream)
    async with _serve(model, [run_command]) as (conn, recorder, _):
        session_id = await _new_session(conn, str(tmp_path))
        await conn.prompt(_prompt(session_id))
        await recorder.wait_idle()

    terminals = recorder.of(v2.TerminalUpdate)
    assert terminals[0].command == "echo hi"
    assert terminals[0].cwd == str(tmp_path)
    assert [c.decode() for c in recorder.of(v2.TerminalOutputChunk)] == [b"hi\n"]
    texts = [
        c
        for u in recorder.of(v2.ToolCallSessionUpdate)
        if v2.is_set(u.content)
        for c in u.content or []
        if isinstance(c, v2.Content)
    ]
    assert not texts  # The output is in the terminal, not repeated as text
    exit_status = terminals[-1].exit_status
    assert isinstance(exit_status, v2.TerminalExitStatus)
    assert exit_status.exit_code == 0
    # The terminal is known before the tool call showing it
    first_terminal = next(
        i for i, u in enumerate(recorder.updates) if isinstance(u, v2.TerminalUpdate)
    )
    showing = next(
        i
        for i, u in enumerate(recorder.updates)
        if isinstance(u, v2.ToolCallSessionUpdate)
        and v2.is_set(u.content)
        and any(isinstance(c, v2.Terminal) for c in u.content or [])
    )
    assert first_terminal < showing


async def test_titles_and_replay_leave_out_the_injected_rules(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model gets the rules; the title model and the replayed history don't."""
    from agentpool.storage.manager import StorageManager

    seen_by_model: list[str] = []
    seen_by_title_model: list[str] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen_by_model.append(str(messages[-1]))
        yield "Done"

    async def title_model(self: StorageManager, session_id: str, prompt_text: str) -> None:
        seen_by_title_model.append(prompt_text)  # Instead of a real model call

    async def rules(project_dir: str | None = None) -> str:
        return "# Rules\n\nRULES-TEXT"

    monkeypatch.setattr(StorageManager, "_generate_title_core", title_model)
    monkeypatch.delenv("PYTEST_CURRENT_TEST")  # Titles are skipped in tests otherwise
    prompt = "Please read the README and tell me what this project is about, briefly"
    # A file database: the in-memory one is a single connection shared by every session
    # (StaticPool), so a concurrent session's rollback could drop the stored turn
    url = f"sqlite+aiosqlite:///{tmp_path}/history.db"
    model = FunctionModel(stream_function=stream)
    async with _serve(model, storage_url=url) as (conn, recorder, agent):
        monkeypatch.setattr(agent, "read_rules", rules)
        session_id = await _new_session(conn, str(tmp_path))
        await conn.prompt(_prompt(session_id, prompt))
        await recorder.wait_idle()

        async def titled() -> None:
            while not seen_by_title_model:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(titled(), TIMEOUT)
        assert "RULES-TEXT" in seen_by_model[0]  # Injected for the model
        assert seen_by_title_model == [f"user: {prompt}"]

        await conn.close_session(v2.CloseSessionRequest(session_id=session_id))
        recorder.updates.clear()
        request = v2.ResumeSessionRequest(
            session_id=session_id, cwd=str(tmp_path), replay_from=v2.ReplayFromStart()
        )
        await conn.resume_session(request)

    replayed = "".join(
        u.content.text
        for u in recorder.updates
        if isinstance(u, v2.UserMessageChunk) and isinstance(u.content, v2.TextContent)
    )
    assert replayed == prompt


async def test_debug_commands_only_with_the_flag(tmp_path: Any) -> None:
    def names(response: v2.NewSessionResponse) -> set[str]:
        commands = response.available_commands if v2.is_set(response.available_commands) else []
        return {c.name for c in commands if isinstance(c, v2.Command)}

    request = v2.NewSessionRequest(cwd=str(tmp_path))
    async with _serve(FunctionModel(stream_function=_reply)) as (conn, _, _):
        await _initialize(conn)
        plain = names(await conn.new_session(request))
    async with _serve(FunctionModel(stream_function=_reply), debug_commands=True) as (conn, _, _):
        await _initialize(conn)
        debug = names(await conn.new_session(request))

    assert "help" in plain
    assert not any(name.startswith("debug-") for name in plain)
    assert "debug-send-text" in debug


async def test_options_without_a_session_list_the_commands(connected: Any, tmp_path: Any) -> None:
    """``session/options`` for a session yet to start: the commands it would have."""
    conn, _, _ = connected
    await _initialize(conn)
    options = await conn.session_options(v2.SessionOptionsRequest(cwd=str(tmp_path)))
    assert v2.is_set(options.available_commands)
    assert "help" in {c.name for c in options.available_commands if isinstance(c, v2.Command)}


async def test_a_fork_has_the_history_and_the_options(connected: Any, tmp_path: Any) -> None:
    """The fork continues the conversation, with the source's mode (and model)."""
    conn, recorder, _ = connected
    session_id = await _new_session(conn, str(tmp_path))
    await conn.prompt(_prompt(session_id, "remember this"))
    await recorder.wait_idle()
    request = v2.SetConfigOptionIdRequest(session_id=session_id, config_id="mode", value="always")
    await conn.set_session_config_option(request)

    fork = await conn.fork_session(v2.ForkSessionRequest(session_id=session_id, cwd=str(tmp_path)))

    options = fork.config_options if v2.is_set(fork.config_options) else []
    mode = next(
        o for o in options if isinstance(o, v2.SelectSessionConfigOption) and o.config_id == "mode"
    )
    assert mode.current_value == "always"
    await conn.close_session(v2.CloseSessionRequest(session_id=fork.session_id))
    recorder.updates.clear()
    resume = v2.ResumeSessionRequest(
        session_id=fork.session_id, cwd=str(tmp_path), replay_from=v2.ReplayFromStart()
    )
    await conn.resume_session(resume)
    replayed = "".join(
        u.content.text
        for u in recorder.updates
        if isinstance(u, v2.UserMessageChunk) and isinstance(u.content, v2.TextContent)
    )
    assert replayed == "remember this"


async def test_session_responses_carry_the_commands(connected: Any, tmp_path: Any) -> None:
    """The commands update at setup may precede the response naming the session."""
    conn, _, _ = connected
    await _initialize(conn)

    new = await conn.new_session(v2.NewSessionRequest(cwd=str(tmp_path)))
    assert v2.is_set(new.available_commands)
    assert "help" in {c.name for c in new.available_commands if isinstance(c, v2.Command)}

    request = v2.ForkSessionRequest(session_id=new.session_id, cwd=str(tmp_path))
    forked = await conn.fork_session(request)
    assert v2.is_set(forked.available_commands)
    assert forked.available_commands

    options_request = v2.SessionOptionsRequest(session_id=new.session_id, cwd=str(tmp_path))
    options = await conn.session_options(options_request)
    assert v2.is_set(options.available_commands)
    assert options.available_commands


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
    monkeypatch.setattr(agent, "turn_running", lambda session_id=None: started.is_set())

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


# --- idempotencyKey (spec: agents advertising capabilities.session MUST deduplicate) ---


def _counting_model(calls: list[int]) -> FunctionModel:
    async def reply(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append(1)
        yield "ok"

    return FunctionModel(stream_function=reply)


def _keyed(session_id: str, key: str, text: str = "hi") -> v2.PromptRequest:
    return v2.PromptRequest(
        session_id=session_id, prompt=[v2.TextContent(text=text)], idempotency_key=key
    )


def _running(recorder: RecordingClient) -> int:
    return len(recorder.of(v2.RunningStateSessionUpdate))


async def test_prompt_retry_with_the_same_key_runs_one_turn(tmp_path: Any) -> None:
    calls: list[int] = []
    async with _serve(_counting_model(calls)) as (conn, recorder, _):
        session_id = await _new_session(conn, str(tmp_path))
        first = await conn.prompt(_keyed(session_id, "submit-1"))
        await recorder.wait_idle()

        retry = await conn.prompt(_keyed(session_id, "submit-1"))
        await asyncio.sleep(0.1)  # a second turn would start right away
        other = await conn.prompt(_keyed(session_id, "submit-2"))
        await recorder.wait_idle()

    assert retry.message_id == first.message_id
    assert other.message_id != first.message_id
    assert len(calls) == 2  # submit-1 once, submit-2 once
    assert _running(recorder) == 2


async def test_concurrent_attempts_with_one_key_insert_once(tmp_path: Any) -> None:
    calls: list[int] = []
    async with _serve(_counting_model(calls)) as (conn, recorder, _):
        session_id = await _new_session(conn, str(tmp_path))
        responses = await asyncio.gather(
            *(conn.prompt(_keyed(session_id, "same")) for _ in range(3))
        )
        await recorder.wait_idle()
        await asyncio.sleep(0.1)

    assert len({r.message_id for r in responses}) == 1
    assert len(calls) == 1


async def test_keys_are_scoped_to_their_session(tmp_path: Any) -> None:
    calls: list[int] = []
    async with _serve(_counting_model(calls)) as (conn, recorder, _):
        first_session = await _new_session(conn, str(tmp_path))
        second_session = (
            await conn.new_session(v2.NewSessionRequest(cwd=str(tmp_path)))
        ).session_id
        first = await conn.prompt(_keyed(first_session, "k"))
        await recorder.wait_idle()
        second = await conn.prompt(_keyed(second_session, "k"))
        await recorder.wait_idle()

    assert first.message_id != second.message_id
    assert len(calls) == 2


@pytest.mark.parametrize("key", ["", "x" * 256, 42])
async def test_invalid_idempotency_keys_are_invalid_params(
    connected: Any, tmp_path: Any, key: object
) -> None:
    conn, recorder, _ = connected
    session_id = await _new_session(conn, str(tmp_path))
    params = {"sessionId": session_id, "prompt": [{"type": "text", "text": "hi"}]}
    with pytest.raises(RequestError) as exc_info:
        await conn.connection.send_request("session/prompt", {**params, "idempotencyKey": key})

    assert exc_info.value.code == -32602
    await asyncio.sleep(0.1)
    assert _running(recorder) == 0


async def test_keys_survive_a_restart(tmp_path: Any) -> None:
    """The keys are in the session storage: a retry after a restart inserts nothing."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}"
    calls: list[int] = []
    async with _serve(_counting_model(calls), storage_url=url) as (conn, recorder, _):
        session_id = await _new_session(conn, str(tmp_path))
        first = await conn.prompt(_keyed(session_id, "before-restart"))
        await recorder.wait_idle()
    assert len(calls) == 1

    async with _serve(_counting_model(calls), storage_url=url) as (conn, recorder, _):
        await _initialize(conn)
        retry = await conn.prompt(_keyed(session_id, "before-restart"))
        await asyncio.sleep(0.2)
        assert _running(recorder) == 0
        fresh = await conn.prompt(_keyed(session_id, "after-restart"))
        await recorder.wait_idle()

    assert retry.message_id == first.message_id
    assert fresh.message_id != first.message_id
    assert len(calls) == 2


async def test_unknown_mcp_transport_is_invalid_params_and_launches_nothing(
    connected: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v2 reads an unknown transport as UnknownMcpServer; v1 must not run it as stdio."""
    launched: list[Any] = []

    async def initialize_mcp_servers(self: acp_session.ACPSession) -> None:
        launched.extend(self.mcp_servers or [])

    monkeypatch.setattr(acp_session.ACPSession, "initialize_mcp_servers", initialize_mcp_servers)
    conn, _, _ = connected
    await _initialize(conn)
    server = {"type": "websocket", "name": "x", "command": "/bin/sh", "args": [], "env": []}
    params = {"cwd": str(tmp_path), "mcpServers": [server]}
    with pytest.raises(RequestError) as exc_info:
        await conn.connection.send_request("session/new", params)

    assert exc_info.value.code == -32602
    assert launched == []


def _capabilities(recorder: RecordingClient, tool_call_id: str) -> list[Any]:
    """The capability changes sent for a tool call, in order."""
    return [
        u.capabilities
        for u in recorder.of(v2.ToolCallSessionUpdate)
        if u.tool_call_id == tool_call_id and v2.is_set(u.capabilities)
    ]


async def test_a_background_task_is_cancelled_through_its_tool_call(tmp_path: Any) -> None:
    """``tool_call/cancel`` on the tool call that started a stoppable task stops it."""
    from agentpool.agents.agent_tasks import TaskOutput
    from agentpool.agents.events import TaskFinishedEvent, TaskStartedEvent

    async def start_job(ctx: AgentContext) -> str:
        """Start a job in the background."""

        async def job() -> None:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                finished = TaskFinishedEvent(task_id="job1", status="cancelled")
                await ctx.agent.emit_event(finished)
                raise

        async def no_output() -> TaskOutput:
            return TaskOutput(output="", total_bytes=0)

        started = TaskStartedEvent(
            task_id="job1",
            kind="shell",
            description="job",
            tool_call_id=ctx.tool_call_id,
            background=True,
            stoppable=True,
        )
        await ctx.events.emit_event(started)
        ctx.agent.register_local_task("job1", asyncio.create_task(job()), no_output)
        return "started"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="start_job", json_args="{}", tool_call_id="c1")}
        else:
            yield "the job runs"

    async with _serve(FunctionModel(stream_function=stream), [start_job]) as (conn, recorder, _):
        session_id = await _new_session(conn, str(tmp_path))
        await conn.prompt(_prompt(session_id))
        assert (await recorder.wait_idle()).stop_reason == "end_turn"  # The job outlives it
        [offered] = _capabilities(recorder, "c1")
        assert isinstance(offered, v2.ToolCallCapabilities)
        assert v2.is_set(offered.cancel)

        request = v2.CancelToolCallRequest(session_id=session_id, tool_call_id="c1")
        await conn.cancel_tool_call(request)
        for _ in range(100):  # The job reports its end between turns
            if len(_capabilities(recorder, "c1")) == 2:
                break
            await asyncio.sleep(0.02)
        assert _capabilities(recorder, "c1")[-1] is None  # Withdrawn once it ended

        with pytest.raises(RequestError):  # Nothing left to cancel
            await conn.cancel_tool_call(request)


async def _parent_idle(recorder: RecordingClient, session_id: str) -> v2.IdleStateSessionUpdate:
    """The first idle state of ``session_id`` itself (children report theirs too)."""
    for _ in range(250):
        for update in _under(recorder, session_id):
            if isinstance(update, v2.IdleStateSessionUpdate):
                return update
        await asyncio.sleep(0.02)
    raise AssertionError(f"{session_id} never went idle")


def _under(recorder: RecordingClient, session_id: str) -> list[v2.SessionUpdate]:
    """The updates sent under ``session_id``, in order."""
    return [n.update for n in recorder.notifications if n.session_id == session_id]


async def test_a_subagent_run_is_a_child_session(tmp_path: Any) -> None:
    """The task tool's run shows as a child session: announced, linked, its own updates."""
    from pydantic_ai.models.test import TestModel

    from agentpool_config.toolsets import SubagentToolsetConfig

    async def lead(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if len(messages) == 1:
            args = {"agent_or_team": "helper", "prompt": "say hi", "description": "Greet"}
            yield {0: DeltaToolCall(name="task", json_args=json.dumps(args), tool_call_id="c1")}
        else:
            yield "delegated"

    helpers = {"helper": TestModel(custom_output_text="hi from the helper")}
    toolsets = [SubagentToolsetConfig().get_provider()]
    storage_url = f"sqlite+aiosqlite:///{tmp_path / 'history.db'}"  # Shared by both agents
    served = _serve(
        FunctionModel(stream_function=lead),
        toolsets=toolsets,
        helpers=helpers,
        storage_url=storage_url,
    )
    async with (
        served as (
            conn,
            recorder,
            _,
        )
    ):
        session_id = await _new_session(conn, str(tmp_path))
        await conn.prompt(_prompt(session_id))
        idle = await _parent_idle(recorder, session_id)  # The child's idle comes first
        assert idle.stop_reason == "end_turn"

    announcements = [u for u in _under(recorder, session_id) if isinstance(u, v2.SubagentUpdate)]
    child_id = announcements[0].session_id
    assert announcements[0].title == "helper"
    assert isinstance(announcements[0].state, v2.RunningStateUpdate)
    # Announced before any update under the child's ID
    first_child = next(i for i, n in enumerate(recorder.notifications) if n.session_id == child_id)
    announced_at = next(
        i
        for i, n in enumerate(recorder.notifications)
        if isinstance(n.update, v2.SubagentUpdate) and n.update.session_id == child_id
    )
    assert announced_at < first_child
    # The task tool call shows it
    links = [u for u in _under(recorder, session_id) if isinstance(u, v2.ToolCallContentChunk)]
    assert any(
        isinstance(u.content, v2.SessionReference) and u.content.session_id == child_id
        for u in links
    )
    # The helper's answer is the child's, not the parent's
    child_text = "".join(
        u.content.text
        for u in _under(recorder, child_id)
        if isinstance(u, v2.AgentMessageChunk) and isinstance(u.content, v2.TextContent)
    )
    assert "hi from the helper" in child_text
    parent_text = "".join(
        u.content.text
        for u in _under(recorder, session_id)
        if isinstance(u, v2.AgentMessageChunk) and isinstance(u.content, v2.TextContent)
    )
    assert "hi from the helper" not in parent_text
    # It ended: idle on the child, mirrored on the parent's association
    state_kinds = (
        v2.RunningStateSessionUpdate,
        v2.IdleStateSessionUpdate,
        v2.RequiresActionStateSessionUpdate,
        v2.UnknownStateSessionUpdate,
    )
    child_states = [u for u in _under(recorder, child_id) if isinstance(u, state_kinds)]
    assert isinstance(child_states[-1], v2.IdleStateSessionUpdate)
    assert child_states[-1].stop_reason == "end_turn"
    mirrored = [a.state for a in announcements if v2.is_set(a.state)]
    assert isinstance(mirrored[-1], v2.IdleStateUpdate)


async def test_a_stoppable_child_is_cancelled_and_refuses_other_methods(tmp_path: Any) -> None:
    """A child advertising cancel stops its task on session/cancel; prompt is refused."""
    from pydantic_ai import TextPart

    from agentpool.agents.agent_tasks import TaskOutput
    from agentpool.agents.events import (
        PartStartEvent,
        SubAgentEvent,
        TaskFinishedEvent,
        TaskStartedEvent,
    )

    async def start_research(ctx: AgentContext) -> str:
        """Start a research subagent in the background."""

        async def research() -> None:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                finished = TaskFinishedEvent(task_id="r1", status="cancelled")
                await ctx.agent.emit_event(finished)
                raise

        async def no_output() -> TaskOutput:
            return TaskOutput(output="", total_bytes=0)

        started = TaskStartedEvent(
            task_id="r1",
            kind="agent",
            description="research",
            tool_call_id=ctx.tool_call_id,
            background=True,
            stoppable=True,
        )
        await ctx.events.emit_event(started)
        ctx.agent.register_local_task("r1", asyncio.create_task(research()), no_output)
        text = PartStartEvent(index=0, part=TextPart(content="looking into it"))
        await ctx.events.emit_event(
            SubAgentEvent(
                source_name="researcher",
                source_type="agent",
                event=text,
                parent_tool_call_id=ctx.tool_call_id,
            )
        )
        return "started"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="start_research", json_args="{}", tool_call_id="c1")}
        else:
            yield "it runs"

    async with _serve(FunctionModel(stream_function=stream), [start_research]) as (
        conn,
        recorder,
        _,
    ):
        session_id = await _new_session(conn, str(tmp_path))
        await conn.prompt(_prompt(session_id))
        idle = await _parent_idle(recorder, session_id)
        assert idle.stop_reason == "end_turn"  # The child goes on
        [announce, *_] = [
            u for u in _under(recorder, session_id) if isinstance(u, v2.SubagentUpdate)
        ]
        child_id = announce.session_id
        assert isinstance(announce.capabilities, v2.SubagentSessionCapabilities)
        assert v2.is_set(announce.capabilities.cancel)

        with pytest.raises(RequestError):
            await conn.prompt(_prompt(child_id))
        await conn.cancel(v2.CancelSessionNotification(session_id=child_id))
        for _ in range(100):
            states = [
                u for u in _under(recorder, child_id) if isinstance(u, v2.IdleStateSessionUpdate)
            ]
            if states:
                break
            await asyncio.sleep(0.02)
        assert states[-1].stop_reason == "cancelled"
