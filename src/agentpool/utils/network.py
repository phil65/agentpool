"""Small helpers for the localhost servers agentpool runs (tool bridge, code-mode tools)."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import anyio


if TYPE_CHECKING:
    import asyncio
    import socket

    from starlette.types import ASGIApp, Receive, Scope, Send
    from uvicorn import Server


class BearerAuth:
    """ASGI middleware: refuse HTTP requests that don't carry ``token`` as bearer token.

    For servers on localhost, which any local process could reach otherwise.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            given = dict(scope["headers"]).get(b"authorization", b"")
            if not secrets.compare_digest(given, self._expected):
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                })
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await self.app(scope, receive, send)


async def wait_until_started(server: Server, task: asyncio.Task[None]) -> None:
    """Wait until uvicorn ``server`` (run by ``task``) serves.

    Raises:
        RuntimeError: It exited without starting (a failed startup it logged itself)
    """
    while not server.started:
        if task.done():
            task.result()  # Its error, if it raised one
            raise RuntimeError("Server exited during startup")
        await anyio.sleep(0.01)


def _create_socket(preferred_port: int) -> tuple[socket.socket, int]:
    """Create a socket bound to a free port.

    Returns:
        A tuple containing the socket and the port it is bound to.
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        # Try preferred port first
        sock.bind(("127.0.0.1", preferred_port))
    except OSError:
        # Fall back to any available port
        sock.bind(("127.0.0.1", 0))
    return sock, sock.getsockname()[1]  # The bound port (also for preferred_port=0)
