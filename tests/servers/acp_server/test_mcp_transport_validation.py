"""v1 session setup with an unknown or malformed MCP transport: -32602, nothing launched."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from aclientp.agent.connection import AgentSideConnection
import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream
from pydantic_ai.models.function import FunctionModel
import pytest

from agentpool import Agent, AgentPool, AgentsManifest
from agentpool_config.storage import SQLStorageConfig, StorageConfig
from agentpool_server.acp_server import session as acp_session
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream


TIMEOUT = 5
STDIO = {"name": "evil", "command": "/bin/sh", "args": ["-c", "true"], "env": []}


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


class _Peer:
    """The client side, as raw JSON-RPC frames."""

    def __init__(
        self, send: MemoryObjectSendStream[bytes], receive: MemoryObjectReceiveStream[bytes]
    ) -> None:
        self._send = send
        self._receive = receive
        self._buffer = b""

    async def request(self, request_id: int, method: str, params: Any) -> dict[str, Any]:
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        await self._send.send(json.dumps(frame).encode() + b"\n")
        with anyio.fail_after(TIMEOUT):
            while True:
                while b"\n" in self._buffer:
                    line, self._buffer = self._buffer.split(b"\n", 1)
                    message: dict[str, Any] = json.loads(line)
                    if message.get("id") == request_id and "method" not in message:
                        return message
                self._buffer += await self._receive.receive()


@pytest.fixture
async def peer(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[_Peer, list[Any]]]:
    launched: list[Any] = []

    async def initialize_mcp_servers(self: acp_session.ACPSession) -> None:
        launched.extend(self.mcp_servers or [])

    monkeypatch.setattr(acp_session.ACPSession, "initialize_mcp_servers", initialize_mcp_servers)
    storage = StorageConfig(providers=[SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")])
    async with AgentPool(AgentsManifest(storage=storage)) as pool:
        agent = Agent(name="a", model=FunctionModel(lambda m, i: None), agent_pool=pool)  # type: ignore[arg-type, return-value]
        pool.register("a", agent)
        to_agent_send, to_agent_recv = anyio.create_memory_object_stream[bytes](100)
        to_client_send, to_client_recv = anyio.create_memory_object_stream[bytes](100)
        conn = AgentSideConnection(
            lambda conn: AgentPoolACPAgent(client=conn, default_agent=agent),
            _Writer(to_client_send),
            _Reader(to_agent_recv),
        )
        client = _Peer(to_agent_send, to_client_recv)
        reply = await client.request(0, "initialize", {"protocolVersion": 1})
        assert "result" in reply
        yield client, launched
        await conn.close()


@pytest.mark.parametrize(
    "server",
    [
        {**STDIO, "type": "http", "url": 42},  # malformed http: not a stdio fallback
        {**STDIO, "type": "websocket"},  # unknown transport
    ],
)
@pytest.mark.parametrize("method", ["session/new", "session/load", "session/resume"])
async def test_rejected_transports_answer_invalid_params(
    peer: tuple[_Peer, list[Any]], server: dict[str, Any], method: str, tmp_path: Any
) -> None:
    client, launched = peer
    params: dict[str, Any] = {"cwd": str(tmp_path), "mcpServers": [server]}
    if method != "session/new":
        params["sessionId"] = "ses_unknown"

    reply = await client.request(1, method, params)

    assert reply["error"]["code"] == -32602
    assert launched == []


async def test_stdio_without_type_still_launches(
    peer: tuple[_Peer, list[Any]], tmp_path: Any
) -> None:
    client, launched = peer

    reply = await client.request(1, "session/new", {"cwd": str(tmp_path), "mcpServers": [STDIO]})

    assert "result" in reply, reply
    assert [server.command for server in launched] == ["/bin/sh"]
