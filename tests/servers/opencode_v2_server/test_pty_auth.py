"""PTY websocket auth (#44.1): the HTTP auth middleware does not see websockets."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from httpx2 import ASGITransport, AsyncClient
import pytest

from agentpool_server.opencode_v2_server.event_store import EventStore
from agentpool_server.opencode_v2_server.server import create_app


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, MutableMapping
    from pathlib import Path

    from fastapi import FastAPI


PASSWORD = "s3cret"
CREDENTIAL = base64.b64encode(f"opencode:{PASSWORD}".encode()).decode()
PTY_ID = "pty_missing"


@pytest.fixture
async def secured_app(
    pool: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[FastAPI]:
    """The V2 app with a password."""
    monkeypatch.delenv("OPENCODE_PASSWORD", raising=False)
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)
    app = create_app(
        pool, working_dir=str(tmp_path), password=PASSWORD, store=EventStore(tmp_path / "ev")
    )
    async with app.router.lifespan_context(app):
        yield app


async def ws_handshake(
    app: FastAPI, *, query: str = "", headers: dict[str, str] | None = None
) -> MutableMapping[str, Any]:
    """Open the PTY websocket and return the app's first answer (accept or close)."""
    raw_headers = [(b"host", b"127.0.0.1:4096")]
    raw_headers += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": f"/api/pty/{PTY_ID}/connect",
        "raw_path": f"/api/pty/{PTY_ID}/connect".encode(),
        "query_string": query.encode(),
        "headers": raw_headers,
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 4096),
        "subprotocols": [],
        "state": {},
    }
    incoming = [{"type": "websocket.connect"}]
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return incoming.pop(0) if incoming else {"type": "websocket.disconnect", "code": 1000}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent[0]


async def test_websocket_without_credentials_is_rejected(secured_app: FastAPI) -> None:
    answer = await ws_handshake(secured_app)
    assert answer["type"] == "websocket.close"
    assert answer["code"] == 4401


async def test_websocket_with_wrong_password_is_rejected(secured_app: FastAPI) -> None:
    bad = base64.b64encode(b"opencode:wrong").decode()
    answer = await ws_handshake(secured_app, headers={"Authorization": f"Basic {bad}"})
    assert answer["type"] == "websocket.close"


async def test_websocket_from_foreign_origin_is_rejected(secured_app: FastAPI) -> None:
    headers = {"Authorization": f"Basic {CREDENTIAL}", "Origin": "https://evil.example"}
    answer = await ws_handshake(secured_app, headers=headers)
    assert answer["type"] == "websocket.close"
    assert answer["code"] == 4403


@pytest.mark.parametrize(
    ("query", "headers"),
    [
        ("", {"Authorization": f"Basic {CREDENTIAL}"}),
        (f"auth_token={CREDENTIAL}", {}),
        ("", {"Authorization": f"Basic {CREDENTIAL}", "Origin": "http://127.0.0.1:4096"}),
    ],
)
async def test_websocket_with_password_is_accepted(
    secured_app: FastAPI, query: str, headers: dict[str, str]
) -> None:
    answer = await ws_handshake(secured_app, query=query, headers=headers)
    assert answer["type"] == "websocket.accept"


async def test_websocket_with_ticket_is_accepted_once(secured_app: FastAPI) -> None:
    ptys = secured_app.state.v2_state.ptys
    ticket = ptys.issue_ticket(PTY_ID)
    answer = await ws_handshake(secured_app, query=f"ticket={ticket}")
    assert answer["type"] == "websocket.accept"
    again = await ws_handshake(secured_app, query=f"ticket={ticket}")
    assert again["type"] == "websocket.close"


async def test_connect_token_needs_the_ticket_header_and_an_allowed_origin(
    secured_app: FastAPI,
) -> None:
    # Only the PTY's ID matters to connect-token: a stand-in, so no real PTY is needed
    sessions = secured_app.state.v2_state.ptys.sessions
    sessions[PTY_ID] = object()
    transport = ASGITransport(app=secured_app)
    auth = {"Authorization": f"Basic {CREDENTIAL}"}
    url = f"/api/pty/{PTY_ID}/connect-token"
    try:
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1:4096") as http:
            assert (await http.post(url, headers=auth)).status_code == 403
            foreign = {**auth, "x-opencode-ticket": "1", "Origin": "https://evil.example"}
            assert (await http.post(url, headers=foreign)).status_code == 403
            response = await http.post(url, headers={**auth, "x-opencode-ticket": "1"})
            assert response.status_code == 200
            ticket = response.json()["data"]["ticket"]
    finally:
        del sessions[PTY_ID]
    assert secured_app.state.v2_state.ptys.consume_ticket(ticket, PTY_ID)
