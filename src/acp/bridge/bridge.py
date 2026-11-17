"""ACP Bridge - Proxy stdio ACP agents to streamable HTTP transport.

This module provides functionality to spawn a stdio-based ACP agent subprocess
and expose it via a streamable HTTP endpoint.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
import uvicorn

from acp.agent.connection import _agent_handler
from acp.bridge.settings import BridgeSettings
from acp.client.connection import ClientSideConnection
from acp.client.implementations import NoOpClient
from acp.transports import spawn_stdio_transport


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from anyio.abc import Process
    from starlette.requests import Request

    from acp.schema import AgentMethod


logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class ACPBridge:
    """Bridge that proxies stdio ACP agents to streamable HTTP."""

    command: str
    """ACP Agent shell command which should get spawned by the bridge."""

    args: list[str] = field(default_factory=list)
    """Arguments for the command."""

    env: Mapping[str, str] | None = None
    """Environment variables for the subprocess."""

    cwd: str | Path | None = None
    """Working directory for the subprocess."""

    settings: BridgeSettings = field(default_factory=BridgeSettings)
    """Bridge server settings."""

    def __post_init__(self) -> None:
        self._connection: ClientSideConnection | None = None
        self._process: Process | None = None

    async def _handle_acp_request(self, request: Request) -> Response:
        """Handle incoming ACP JSON-RPC requests."""
        if self._connection is None:
            response = {"jsonrpc": "2.0", "error": {"code": -32603, "message": "Not connected"}}
            return JSONResponse(response, status_code=503)

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            response = {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}
            return JSONResponse(response, status_code=400)

        method = body.get("method")
        params = body.get("params")
        request_id = body.get("id")
        is_notification = request_id is None

        if method is None:
            error = {"code": -32600, "message": "Invalid Request"}
            response = {"jsonrpc": "2.0", "id": request_id, "error": error}
            return JSONResponse(response, status_code=400)

        try:
            result = await self._dispatch_to_agent(method, params, is_notification)
            if is_notification:
                return Response(status_code=204)
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:
            logger.exception("Error dispatching request to agent")
            err = {"code": -32603, "message": str(exc)}
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": err}, status_code=500)

    async def _dispatch_to_agent(
        self,
        method: AgentMethod | str,
        params: dict[str, Any] | None,
        is_notification: bool,
    ) -> Any:
        """Dispatch a request to the connected agent."""
        if self._connection is None:
            raise RuntimeError("No agent connection")
        # The client-side connection implements the Agent protocol, so the regular
        # agent dispatcher forwards every method to the subprocess.
        result = await _agent_handler(self._connection, method, params, is_notification)
        if isinstance(result, BaseModel):
            return result.model_dump(by_alias=True, exclude_none=True)
        return result

    async def _handle_status(self, request: Request) -> Response:
        """Health check endpoint."""
        status = "connected" if self._connection else "disconnected"
        return JSONResponse({"status": status, "command": self.command, "args": self.args})

    def _create_app(self) -> Starlette:
        """Create the Starlette application."""
        routes = [
            Route("/acp", endpoint=self._handle_acp_request, methods=["POST"]),
            Route("/status", endpoint=self._handle_status, methods=["GET"]),
        ]

        middleware: list[Middleware] = []
        if self.settings.allow_origins:
            mw = Middleware(
                CORSMiddleware,
                allow_origins=self.settings.allow_origins,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            middleware.append(mw)
        is_debug = self.settings.log_level == "DEBUG"
        return Starlette(debug=is_debug, routes=routes, middleware=middleware)

    async def run(self) -> None:
        """Run the bridge server."""
        async with contextlib.AsyncExitStack() as stack:
            # Spawn the stdio agent subprocess
            logger.info("Spawning ACP agent: %s %s", self.command, " ".join(self.args))
            coro = spawn_stdio_transport(self.command, *self.args, env=self.env, cwd=self.cwd)
            reader, writer, process = await stack.enter_async_context(coro)
            self._process = process

            # Create client connection to the agent
            def client_factory(agent: Any) -> NoOpClient:
                return NoOpClient()

            self._connection = ClientSideConnection(client_factory, writer, reader)
            stack.push_async_callback(self._connection.close)
            app = self._create_app()  # Create and run the HTTP server
            config = uvicorn.Config(
                app,
                host=self.settings.host,
                port=self.settings.port,
                log_level=self.settings.log_level.lower(),
            )
            server = uvicorn.Server(config)
            url = f"http://{self.settings.host}:{self.settings.port}"
            logger.info("ACP Bridge serving at %s/acp", url)
            await server.serve()
