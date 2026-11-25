"""OpenCode V2 protocol server.

Exposes an :class:`~agentpool.AgentPool` through the OpenCode V2 HTTP API so the
OpenCode V2 TUI, web and desktop clients can drive pool agents::

    opencode --server http://127.0.0.1:4096

Unlike the V1 server, this server is pool-wide: every session is bound to one pool node
(agent or team) and the client can switch it at any time.
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentpool.log import get_logger
from agentpool_server.base import BaseServer
from agentpool_server.opencode_v2_server.errors import ApiError, invalid_request, unauthorized
from agentpool_server.opencode_v2_server.events import dump_model
from agentpool_server.opencode_v2_server.routes import (
    agents,
    files,
    interactions,
    misc,
    pty as pty_routes,
    runtime,
    server as server_routes,
    session_ops,
    sessions,
)
from agentpool_server.opencode_v2_server.state import ServerState


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from starlette.responses import Response

    from agentpool import AgentPool
    from agentpool_server.opencode_v2_server.event_store import EventStore


logger = get_logger(__name__)

AUTH_USERNAME = "opencode"
CORS_ORIGIN_REGEX = (
    r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?"
    r"|https://([a-z0-9-]+\.)*opencode\.ai"
    r"|tauri://localhost|https?://tauri\.localhost|oc://renderer)$"
)


def resolve_password(password: str | None = None) -> str | None:
    """Server password: explicit value, ``OPENCODE_PASSWORD`` or ``OPENCODE_SERVER_PASSWORD``."""
    return (
        password
        or os.environ.get("OPENCODE_PASSWORD")
        or os.environ.get("OPENCODE_SERVER_PASSWORD")
        or None
    )


def _authorized(header: str | None, password: str) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip()).decode()
    except ValueError, UnicodeDecodeError:
        return False
    username, _, given = decoded.partition(":")
    return username == AUTH_USERNAME and hmac.compare_digest(given, password)


def create_app(
    pool: AgentPool[Any],
    *,
    working_dir: str | None = None,
    password: str | None = None,
    host: str = "127.0.0.1",
    port: int | None = None,
    store: EventStore | None = None,
) -> FastAPI:
    """Create the OpenCode V2 FastAPI application.

    The pool must already be entered (``async with pool``); the app does not manage it.

    Args:
        pool: The agent pool to expose
        working_dir: Default location directory (defaults to the current directory)
        password: Basic auth password (defaults to ``OPENCODE_PASSWORD``); None disables auth
        host: Host the server is reachable at (reported by ``/api/info``)
        port: Port the server is reachable at (reported by ``/api/info``)
        store: Event store for session logs (defaults to the AgentPool data directory)
    """
    from agentpool import __version__

    state = ServerState(
        pool=pool,
        working_dir=working_dir or str(Path.cwd()),
        host=host,
        port=port,
    )
    if store is not None:
        state.store = store
    secret = resolve_password(password)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        state.connect_signals()
        try:
            yield
        finally:
            state.disconnect_signals()
            await state.shutdown()

    app = FastAPI(
        title="AgentPool OpenCode V2 API",
        description="OpenCode V2 HTTP API served by AgentPool",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.v2_state = state

    @app.middleware("http")
    async def basic_auth(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        needs_auth = secret is not None and request.method != "OPTIONS"
        if needs_auth and not _authorized(request.headers.get("authorization"), secret or ""):
            error = unauthorized()
            return JSONResponse(
                dump_model(error.body),
                status_code=error.status_code,
                headers={"WWW-Authenticate": 'Basic realm="opencode"'},
            )
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=CORS_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(dump_model(exc.body), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        location = first.get("loc", ())
        field = ".".join(str(part) for part in location[1:]) or None
        message = str(first.get("msg", "Invalid request"))
        error = invalid_request(message, field=field)
        return JSONResponse(dump_model(error.body), status_code=error.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> Response:
        # Like OpenCode: unknown endpoints answer without a JSON body, so clients can tell
        # "endpoint not implemented" apart from typed API errors.
        if exc.status_code == 404:  # noqa: PLR2004
            return PlainTextResponse("Not Found", status_code=404)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    for module in (
        server_routes,
        agents,
        sessions,
        session_ops,
        interactions,
        files,
        runtime,
        pty_routes,
        misc,
    ):
        app.include_router(module.router)
    return app


class OpenCodeV2Server(BaseServer):
    """OpenCode V2 protocol server for an agent pool."""

    def __init__(
        self,
        pool: AgentPool[Any],
        *,
        name: str | None = None,
        host: str = "127.0.0.1",
        port: int = 4096,
        working_dir: str | None = None,
        password: str | None = None,
        raise_exceptions: bool = False,
    ) -> None:
        """Initialize the server.

        Args:
            pool: The agent pool to expose
            name: Optional server name
            host: Host to bind to
            port: Port to listen on
            working_dir: Default location directory
            password: Basic auth password (defaults to ``OPENCODE_PASSWORD``)
            raise_exceptions: Whether to raise exceptions during server start
        """
        super().__init__(pool, name=name, raise_exceptions=raise_exceptions)
        self.host = host
        self.port = port
        self.working_dir = working_dir
        self.password = password
        self._app: FastAPI | None = None

    @property
    def app(self) -> FastAPI:
        """The FastAPI application (created lazily)."""
        if self._app is None:
            self._app = create_app(
                self.pool,
                working_dir=self.working_dir,
                password=self.password,
                host=self.host,
                port=self.port,
            )
        return self._app

    async def _start_async(self) -> None:
        import uvicorn

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            ws="websockets-sansio",
            log_level="warning",
        )
        server = uvicorn.Server(config)
        await server.serve()
