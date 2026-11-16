"""Debug server combining ACP protocol with FastAPI for manual testing.

This module provides a debug server that runs both:
1. An ACP (Agent Client Protocol) server for testing client integration
2. A FastAPI web server for manually triggering all notification types

The server allows developers to test ACP client implementations by providing
mock responses and the ability to manually send any notification type from
the ACP schema through a web interface.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
import sys
import threading
import time
from typing import TYPE_CHECKING, Any
import uuid

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import structlog
import uvicorn

from acp import AgentSideConnection
from acp.agent.implementations.debug_server.mock_agent import MockAgent
from acp.agent.implementations.debug_server.models import (
    DebugState,
    DebugStatus,
    NotificationRecord,
    NotificationRequest,
)
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AvailableCommand,
    AvailableCommandsUpdate,
    ContentToolCallContent,
    CurrentModeUpdate,
    PlanEntry,
    SessionNotification,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)
from acp.stdio import stdio_streams


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from acp.agent.protocol import Agent
    from acp.schema import SessionUpdate


logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan manager."""
    logger.info("Debug server FastAPI starting up")
    yield
    logger.info("Debug server FastAPI shutting down")


# Create FastAPI app
app = FastAPI(
    title="ACP Debug Server",
    description="Debug interface for Agent Client Protocol testing",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
async def debug_interface() -> HTMLResponse:
    """Serve debug interface HTML."""
    content = Path(__file__).parent / "debug.html"
    return HTMLResponse(content.read_text("utf-8"))


@app.get("/status")
async def get_status() -> DebugStatus:
    """Get current debug server status."""
    state = _get_debug_state()
    return DebugStatus(
        active_sessions=list(state.sessions.keys()),
        current_session=state.active_session_id,
        notifications_sent=len(state.notifications_sent),
        acp_connected=state.client_connection is not None,
    )


@app.post("/send-notification")
async def send_notification(request: NotificationRequest) -> dict[str, Any]:
    """Send a notification through ACP."""
    state = _get_debug_state()

    if not state.client_connection:
        raise HTTPException(status_code=503, detail="ACP client not connected")

    if request.session_id not in state.sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        update = await _create_notification_update(request.notification_type, request.data)
        notification = SessionNotification(session_id=request.session_id, update=update)
        await state.client_connection.session_update(notification)
        record = NotificationRecord(
            notification_type=request.notification_type,
            session_id=request.session_id,
            timestamp=time.perf_counter(),
        )
        state.notifications_sent.append(record)
        logger.info(
            "Sent notification to session",
            notification_type=request.notification_type,
            session_id=request.session_id,
        )
    except Exception as e:
        logger.exception("Failed to send notification")
        raise HTTPException(status_code=500, detail=str(e)) from e
    else:
        return {"success": True, "message": "Notification sent"}


async def _create_notification_update(  # noqa: PLR0911
    notification_type: str, data: dict[str, Any]
) -> SessionUpdate:
    """Create appropriate notification update object."""
    match notification_type:
        case "agent_message":
            return AgentMessageChunk.text(data.get("text", "Mock agent message"))
        case "user_message":
            return UserMessageChunk.text(data.get("text", "Mock user message"))
        case "agent_thought":
            return AgentThoughtChunk.text(data.get("text", "Mock agent thought"))
        case "tool_call_start":
            return ToolCallStart(
                tool_call_id=data.get("tool_call_id", f"tool-{uuid.uuid4()}"),
                title=data.get("title", "Mock Tool Call"),
                status="pending",
                kind=data.get("kind", "other"),
            )
        case "tool_call_progress":
            return ToolCallProgress(
                tool_call_id=data.get("tool_call_id", "tool-123"),
                status=data.get("status", "completed"),
                raw_output=data.get("output"),
                content=[ContentToolCallContent.text(data.get("output", "Tool completed"))]
                if data.get("output")
                else None,
            )
        case "plan_update":
            entries = [
                PlanEntry(content="Mock Plan Entry 1", priority="high", status="completed"),
                PlanEntry(content="Mock Plan Entry 2", priority="medium", status="in_progress"),
            ]
            return AgentPlanUpdate(entries=entries)
        case "commands_update":
            commands = [
                AvailableCommand(name="mock-command", description="A mock command for testing"),
            ]
            return AvailableCommandsUpdate(available_commands=commands)
        case "mode_update":
            return CurrentModeUpdate(current_mode_id=data.get("mode_id", "debug"))
        case _:
            raise ValueError(f"Unknown notification type: {notification_type}")


# Global state reference for FastAPI endpoints (unavoidable with FastAPI)
_global_debug_state: DebugState | None = None


def _set_debug_state(state: DebugState) -> None:
    """Set global debug state reference."""
    global _global_debug_state  # noqa: PLW0603
    _global_debug_state = state


def _get_debug_state() -> DebugState:
    """Get global debug state reference."""
    if _global_debug_state is None:
        raise RuntimeError("Debug state not initialized")
    return _global_debug_state


class ACPDebugServer:
    """Combined ACP and FastAPI debug server."""

    def __init__(self, *, fastapi_port: int = 8000, fastapi_host: str = "127.0.0.1") -> None:
        """Initialize the debug server.

        Args:
            fastapi_port: Port for FastAPI web interface
            fastapi_host: Host for FastAPI web interface
        """
        self.fastapi_port = fastapi_port
        self.fastapi_host = fastapi_host
        self.debug_state = DebugState()
        self.agent = MockAgent(self.debug_state)
        self._running = False
        self._shutdown_event: anyio.Event | None = None
        self._fastapi_thread: threading.Thread | None = None
        # Set global reference for FastAPI endpoints
        _set_debug_state(self.debug_state)

    async def run(self) -> None:
        """Run both ACP server (stdio) and FastAPI server."""
        if self._running:
            raise RuntimeError("Server already running")

        self._running = True
        self._shutdown_event = anyio.Event()
        logger.info("Starting ACP Debug Server")

        try:
            self._start_fastapi()  # Start FastAPI server in background thread
            await self._run_acp_server()  # Start ACP server on stdio
        except Exception:
            logger.exception("Error running debug server")
            raise
        finally:
            await self.shutdown()

    def _start_fastapi(self) -> None:
        """Start FastAPI server in a separate thread."""

        def run_fastapi() -> None:
            uvicorn.run(app, host=self.fastapi_host, port=self.fastapi_port, log_level="info")

        self._fastapi_thread = threading.Thread(target=run_fastapi, daemon=True)
        self._fastapi_thread.start()
        url = f"http://{self.fastapi_host}:{self.fastapi_port}"
        logger.info("FastAPI debug interface started", url=url)

    async def _run_acp_server(self) -> None:
        """Run ACP server on stdio."""
        try:
            logger.info("Starting ACP server on stdio")
            reader, writer = await stdio_streams()

            # Create ACP connection
            def agent_factory(connection: AgentSideConnection) -> Agent:
                return self.agent

            filename = "acp-debug-server.jsonl"
            conn = AgentSideConnection(agent_factory, writer, reader, debug_file=filename)
            # Store connection for FastAPI endpoints
            self.debug_state.client_connection = conn
            logger.info("ACP Debug Server ready - connect your client!")
            url = f"http://{self.fastapi_host}:{self.fastapi_port}"
            logger.info("Web interface", url=url)
            assert self._shutdown_event is not None
            await self._shutdown_event.wait()
        except Exception:
            logger.exception("ACP server error")
            raise

    async def shutdown(self) -> None:
        """Shutdown the debug server."""
        if not self._running:
            raise RuntimeError("Server is not running")

        self._running = False
        if self._shutdown_event:
            self._shutdown_event.set()
        logger.info("Shutting down ACP Debug Server")
        # Clean up connection
        if self.debug_state.client_connection:
            try:
                await self.debug_state.client_connection.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("Error closing ACP connection", error=e)
            finally:
                self.debug_state.client_connection = None


async def main() -> None:
    """Entry point for debug server."""
    import argparse

    parser = argparse.ArgumentParser(description="ACP Debug Server")
    parser.add_argument("--port", type=int, default=7777, help="FastAPI port")
    parser.add_argument("--host", default="127.0.0.1", help="FastAPI host")
    parser.add_argument("--log-level", default="info", help="Logging level")
    args = parser.parse_args()
    # Configure logging
    level = getattr(logging, args.log_level.upper())
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    server = ACPDebugServer(fastapi_port=args.port, fastapi_host=args.host)
    try:
        await server.run()
    except KeyboardInterrupt:
        logger.info("Debug server interrupted")
    except Exception:
        logger.exception("Debug server error")
        sys.exit(1)


if __name__ == "__main__":
    anyio.run(main)
