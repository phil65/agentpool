"""serve-acp over WebSocket / HTTP: token off loopback, handshake auth and Origin checks.

Agents run shell and file tools on the serving machine, so the network transports must
not let anyone (or any web page, for localhost) drive them.
"""

import asyncio
import secrets
import socket
from typing import TYPE_CHECKING, Literal

from aclientp import HttpTransport, WebSocketTransport
from aclientp.client.connection import ClientSideConnection
from aclientp.client.implementations import NoOpClient
from aclientp.schema import InitializeRequest
from aclientp.ws_transport import WebSocketTransportError, connect_remote_transport
import click
import pytest
import typer
from typer.testing import CliRunner

from agentpool_cli.__main__ import cli
from agentpool_cli.serve_acp import _network_transport
from agentpool_server.acp_server import ACPServer


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from agentpool.delegation import AgentPool


TIMEOUT = 15
TOKEN = secrets.token_urlsafe(16)
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("transport", ["websocket", "http"])
def test_cli_refuses_non_loopback_without_token(
    transport: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTPOOL_ACP_TOKEN", raising=False)
    args = ["serve-acp", "--transport", transport, "--host", "0.0.0.0"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 2
    # Rich colours the message on CI (GITHUB_ACTIONS forces colour): compare the plain text.
    assert "requires --token" in click.unstyle(result.output)


@pytest.mark.parametrize("kind", ["websocket", "http"])
def test_network_transport_auth(kind: Literal["websocket", "http"]) -> None:
    with pytest.raises(typer.BadParameter, match="requires --token"):
        _network_transport(kind, "192.0.2.1", 8765, None, [])
    local = _network_transport(kind, "localhost", 8765, None, [])
    assert local.authenticate is None
    exposed = _network_transport(kind, "0.0.0.0", 8765, TOKEN, ["https://app.example"])
    assert exposed.authenticate is not None
    assert exposed.allow_origins == ["https://app.example"]
    assert isinstance(exposed, WebSocketTransport if kind == "websocket" else HttpTransport)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


async def _wait_listening(url: str) -> None:
    for _ in range(400):
        try:
            await _initialize(url, AUTH)
        except WebSocketTransportError:
            await asyncio.sleep(0.025)
        else:
            return
    pytest.fail(f"ACP server at {url} did not start")


async def _initialize(url: str, headers: Mapping[str, str] | None = None) -> None:
    async with connect_remote_transport(url, headers=headers) as (reader, writer):
        conn = ClientSideConnection(lambda _agent: NoOpClient(), writer, reader)
        async with asyncio.timeout(TIMEOUT):
            response = await conn.initialize(InitializeRequest(protocol_version=1))
        assert response.protocol_version == 1
        await conn.close()


@pytest.fixture(params=["websocket", "http"])
async def ws_url(request: pytest.FixtureRequest, mock_agent_pool: AgentPool) -> AsyncIterator[str]:
    """serve-acp's transport with a token, on loopback, as a ws:// URL."""
    port = _free_port()
    transport = _network_transport(request.param, "127.0.0.1", port, TOKEN, [])
    server = ACPServer(mock_agent_pool, transport=transport, load_skills=False)
    url = f"ws://127.0.0.1:{port}" + ("/acp" if request.param == "http" else "")
    async with server:
        # Not start_background(): its task would wait for itself in shutdown()
        task = asyncio.create_task(server.start())
        await _wait_listening(url)
        yield url
        server._shutdown_event.set()  # serve() returns on it
        await asyncio.wait_for(task, TIMEOUT)


async def test_handshake_with_token(ws_url: str) -> None:
    await _initialize(ws_url, AUTH)


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}], ids=["none", "wrong"])
async def test_handshake_without_token_rejected(ws_url: str, headers: dict[str, str]) -> None:
    with pytest.raises(WebSocketTransportError, match=r"\(401\)"):
        await _initialize(ws_url, headers)


async def test_handshake_from_foreign_origin_rejected(ws_url: str) -> None:
    with pytest.raises(WebSocketTransportError, match=r"\(403\)"):
        await _initialize(ws_url, {**AUTH, "Origin": "https://evil.example"})
