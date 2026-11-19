"""Tests for the v2 agent/client runtime and the version router."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import anyio
import pytest

from acp.agent.implementations.testing import TestAgent as V1TestAgent
from acp.exceptions import RequestError
from acp.router import RoutedAgentConnection
from acp.schema.v2 import (
    AgentMessageChunk,
    IdleStateSessionUpdate,
    Implementation,
    InitializeRequest,
    InitializeResponse,
    ListProvidersRequest,
    NewSessionRequest,
    NewSessionResponse,
    PermissionOption,
    PromptRequest,
    PromptResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SelectedPermissionOutcome,
    TextContent,
    UpdateSessionNotification,
)
from acp.v2.agent import Agent, AgentSideConnection
from acp.v2.client import Client, ClientSideConnection
from tests.acp.streams import TIMEOUT, MemoryReader, MemoryWriter, Peer, make_streams


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


INFO = Implementation(name="test", version="1")


class EchoAgent(Agent):
    """A v2 agent that echoes prompts after asking for permission."""

    def __init__(self, conn: AgentSideConnection) -> None:
        self.conn = conn

    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        return InitializeResponse(protocol_version=2, info=INFO)

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        return NewSessionResponse(session_id="s1")

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        permission = await self.conn.request_permission(
            RequestPermissionRequest(
                session_id=params.session_id,
                title="Echo?",
                options=[PermissionOption(option_id="ok", name="OK", kind="allow_once")],
            )
        )
        assert isinstance(permission.outcome, SelectedPermissionOutcome)
        block = params.prompt[0]
        assert isinstance(block, TextContent)
        chunk = AgentMessageChunk(message_id="a1", content=TextContent(text=block.text))
        await self.conn.session_update(
            UpdateSessionNotification(session_id=params.session_id, update=chunk)
        )
        idle = IdleStateSessionUpdate(stop_reason="end_turn")
        await self.conn.session_update(
            UpdateSessionNotification(session_id=params.session_id, update=idle)
        )
        return PromptResponse(message_id="u1")


class RecordingClient(Client):
    """A v2 client that grants every permission and records updates."""

    def __init__(self) -> None:
        self.updates: list[UpdateSessionNotification] = []

    async def request_permission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse:
        option = params.options[0].option_id
        return RequestPermissionResponse(outcome=SelectedPermissionOutcome(option_id=option))

    async def session_update(self, params: UpdateSessionNotification) -> None:
        self.updates.append(params)


def _pipe() -> tuple[MemoryWriter, MemoryReader]:
    send, receive = anyio.create_memory_object_stream[bytes](100)
    return MemoryWriter(send), MemoryReader(receive)


@pytest.fixture
async def connected() -> AsyncIterator[tuple[ClientSideConnection, RecordingClient]]:
    to_agent_writer, to_agent_reader = _pipe()
    to_client_writer, to_client_reader = _pipe()
    agent_conn = AgentSideConnection(EchoAgent, to_client_writer, to_agent_reader)
    client = RecordingClient()
    client_conn = ClientSideConnection(client, to_agent_writer, to_client_reader)
    yield client_conn, client
    await client_conn.close()
    await agent_conn.close()


async def test_client_and_agent_talk_v2(
    connected: tuple[ClientSideConnection, RecordingClient],
) -> None:
    conn, client = connected

    init = await conn.initialize(InitializeRequest(protocol_version=2, info=INFO))
    session = await conn.new_session(NewSessionRequest(cwd="/work"))
    prompt = PromptRequest(session_id=session.session_id, prompt=[TextContent(text="hi")])
    response = await asyncio.wait_for(conn.prompt(prompt), TIMEOUT)
    await asyncio.wait_for(conn.connection.wait_for_notifications(), TIMEOUT)

    assert init.protocol_version == 2
    assert response.message_id == "u1"
    assert [type(u.update) for u in client.updates] == [AgentMessageChunk, IdleStateSessionUpdate]


async def test_unimplemented_agent_method_answers_method_not_found(
    connected: tuple[ClientSideConnection, RecordingClient],
) -> None:
    conn, _ = connected
    await conn.initialize(InitializeRequest(protocol_version=2, info=INFO))

    with pytest.raises(RequestError) as exc:
        await conn.list_providers(ListProvidersRequest())

    assert exc.value.code == -32601


# --- v2 rules, driven with raw frames ------------------------------------------------


@pytest.fixture
async def agent_peer() -> AsyncIterator[Peer]:
    writer, reader, peer = make_streams()
    conn = AgentSideConnection(EchoAgent, writer, reader)
    yield peer
    await conn.close()


def _init(request_id: Any, version: int = 2) -> dict[str, Any]:
    params = {"protocolVersion": version, "info": {"name": "c", "version": "1"}}
    return {"jsonrpc": "2.0", "id": request_id, "method": "initialize", "params": params}


async def test_requests_before_initialize_are_rejected(agent_peer: Peer) -> None:
    new_session = {"method": "session/new", "params": {"cwd": "/w"}}
    await agent_peer.send({"jsonrpc": "2.0", "id": 1, **new_session})

    reply = await agent_peer.receive()

    assert reply["id"] == 1
    assert reply["error"]["code"] == -32600


async def test_initialize_below_v2_is_unsupported(agent_peer: Peer) -> None:
    await agent_peer.send(_init(1, version=1))

    reply = await agent_peer.receive()

    assert reply["error"]["code"] == -32022
    assert reply["error"]["data"] == {"requested": 1, "supported": [2]}


async def test_newer_version_is_answered_as_v2(agent_peer: Peer) -> None:
    await agent_peer.send({**_init(1, version=7), "params": {"protocolVersion": 7}})

    reply = await agent_peer.receive()

    assert reply["result"]["protocolVersion"] == 2


async def test_initialize_cannot_be_repeated_after_other_requests(agent_peer: Peer) -> None:
    await agent_peer.send(_init(1))
    await agent_peer.receive()
    new_session = {"method": "session/new", "params": {"cwd": "/w"}}
    await agent_peer.send({"jsonrpc": "2.0", "id": 2, **new_session})
    await agent_peer.receive()
    await agent_peer.send(_init(3))

    reply = await agent_peer.receive()

    assert reply["id"] == 3
    assert reply["error"]["code"] == -32600


async def test_null_request_ids_and_batches_are_rejected(agent_peer: Peer) -> None:
    await agent_peer.send(_init(None))
    null_id = await agent_peer.receive()
    await agent_peer.send_raw(b'[{"jsonrpc": "2.0", "id": 1, "method": "initialize"}]\n')
    batch = await agent_peer.receive()

    assert null_id["id"] is None
    assert null_id["error"]["code"] == -32600
    assert batch["id"] is None
    assert batch["error"]["code"] == -32600


# --- Router -------------------------------------------------------------------------


@pytest.fixture
async def routed() -> AsyncIterator[tuple[RoutedAgentConnection, Peer]]:
    writer, reader, peer = make_streams()
    conn = RoutedAgentConnection(writer, reader, v1=V1TestAgent(), v2=EchoAgent)
    yield conn, peer
    await conn.close()


async def test_router_selects_v2(routed: tuple[RoutedAgentConnection, Peer]) -> None:
    conn, peer = routed
    await peer.send(_init(1))

    reply = await peer.receive()

    assert reply["result"]["protocolVersion"] == 2
    assert conn.protocol_version == 2
    assert conn.v2_side is not None


async def test_router_falls_back_to_v1(routed: tuple[RoutedAgentConnection, Peer]) -> None:
    conn, peer = routed
    params = {"protocolVersion": 1, "clientCapabilities": {}}
    await peer.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})

    reply = await peer.receive()

    assert reply["result"]["protocolVersion"] == 1
    assert conn.protocol_version == 1
    assert conn.v1_side is not None


async def test_router_rejects_requests_before_initialize(
    routed: tuple[RoutedAgentConnection, Peer],
) -> None:
    _, peer = routed
    await peer.send({"jsonrpc": "2.0", "id": 1, "method": "session/list", "params": {}})

    reply = await peer.receive()

    assert reply["error"]["code"] == -32600


async def test_v2_only_router_rejects_v1() -> None:
    writer, reader, peer = make_streams()
    conn = RoutedAgentConnection(writer, reader, v2=EchoAgent)
    await peer.send(_init(1, version=1))

    reply = await peer.receive()

    assert reply["error"]["code"] == -32022
    await conn.close()
