"""PTY (Pseudo-Terminal) routes.

Uses the agent's execution environment PTY manager for terminal sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from agentpool import log
from agentpool_server.opencode_server.dependencies import StateDep
from opencode_sdk.models import (
    PtyCreatedEvent,
    PtyCreateRequest,
    PtyDeletedEvent,
    PtyExitedEvent,
    PtyInfo,
    PtyUpdatedEvent,
    PtyUpdateRequest,
)


if TYPE_CHECKING:
    from exxec.pty_manager import PtyManagerProtocol

    from agentpool_server.opencode_server.state import ServerState

logger = log.get_logger(__name__)
router = APIRouter(prefix="/pty", tags=["pty"])


@dataclass
class PtySession:
    """Active PTY session with WebSocket subscribers."""

    pty_id: str
    subscribers: set[WebSocket] = field(default_factory=set)
    read_task: asyncio.Task[Any] | None = None
    buffer: str = ""


# Track WebSocket subscribers per PTY session
_pty_sessions: dict[str, PtySession] = {}


def _get_pty_manager(state: StateDep) -> PtyManagerProtocol:
    """Get PTY manager from agent's execution environment."""
    try:
        return state.agent.env.get_pty_manager()
    except NotImplementedError as e:
        detail = "PTY not supported by this execution environment"
        raise HTTPException(status_code=501, detail=detail) from e


@router.get("")
async def list_ptys(state: StateDep) -> list[PtyInfo]:
    """List all PTY sessions."""
    manager = _get_pty_manager(state)
    return [PtyInfo.from_exxec(s) for s in await manager.list_sessions()]


@router.post("")
async def create_pty(request: PtyCreateRequest, state: StateDep) -> PtyInfo:
    """Create a new PTY session."""
    manager = _get_pty_manager(state)
    # Limit number of PTY sessions to prevent resource exhaustion
    sessions = await manager.list_sessions()
    if len(sessions) >= 20:  # Max 20 concurrent PTY sessions  # noqa: PLR2004
        detail = f"Too many PTY sessions ({len(sessions)}). Close some terminals first."
        raise HTTPException(status_code=429, detail=detail)

    # Use working dir from state if not specified
    cwd = request.cwd or state.working_dir
    logger.info("Creating PTY", command=request.command, args=request.args, cwd=cwd)
    try:
        info = await manager.create(
            command=request.command,
            args=request.args,
            cwd=cwd,
            env=request.env,
        )
        logger.info("PTY created successfully", id=info.id, status=info.status)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create PTY: {e}") from e

    pty_id = info.id
    title = request.title or f"Terminal {pty_id[-4:]}"
    # Create session tracker for WebSocket subscribers
    task = asyncio.create_task(_read_pty_output(manager, pty_id, state))
    _pty_sessions[pty_id] = PtySession(pty_id=pty_id, read_task=task)
    logger.info("PTY session registered", pty_id=pty_id, total_sessions=len(_pty_sessions))
    # Start background task to read output and distribute to subscribers
    pty_info = PtyInfo.from_exxec(info, title=title)
    event = PtyCreatedEvent.create(info=pty_info)
    await state.broadcast_event(event)
    return pty_info


async def _read_pty_output(manager: PtyManagerProtocol, pty_id: str, state: ServerState) -> None:
    """Background task to read PTY output and distribute to subscribers."""
    session = _pty_sessions.get(pty_id)
    if not session:
        return

    exit_code = 0
    try:
        async for data in manager.stream(pty_id):
            decoded = data.decode("utf-8", errors="replace")

            if session.subscribers:
                # Send to all connected WebSocket clients
                disconnected: set[WebSocket] = set()
                for ws in session.subscribers:
                    try:
                        await ws.send_text(decoded)
                    except Exception:  # noqa: BLE001
                        disconnected.add(ws)
                session.subscribers -= disconnected
            else:
                # Buffer output if no subscribers
                session.buffer += decoded
                # Limit buffer size
                if len(session.buffer) > 100000:  # noqa: PLR2004
                    session.buffer = session.buffer[-50000:]

    except asyncio.CancelledError:
        return  # Don't broadcast exit if cancelled
    except Exception:  # noqa: BLE001
        exit_code = -1

    # Stream ended - process exited, broadcast event
    event = PtyExitedEvent.create(pty_id=pty_id, exit_code=exit_code)
    await state.broadcast_event(event)


@router.get("/{pty_id}")
async def get_pty(pty_id: str, state: StateDep) -> PtyInfo:
    """Get PTY session details."""
    manager = _get_pty_manager(state)
    info = await manager.get_info(pty_id)
    if not info:
        raise HTTPException(status_code=404, detail="PTY session not found")
    return PtyInfo.from_exxec(info)


@router.put("/{pty_id}")
@router.patch("/{pty_id}")
async def update_pty(pty_id: str, request: PtyUpdateRequest, state: StateDep) -> PtyInfo:
    """Update PTY session (title, resize)."""
    from exxec.pty_manager import PtySize

    manager = _get_pty_manager(state)
    info = await manager.get_info(pty_id)
    if not info:
        raise HTTPException(status_code=404, detail="PTY session not found")

    # Handle resize if requested
    if request.size:
        await manager.resize(pty_id, PtySize(rows=request.size.rows, cols=request.size.cols))
        # Refresh info after resize
        info = await manager.get_info(pty_id)
        if not info:
            raise HTTPException(status_code=404, detail="PTY session not found after resize")

    # Title is handled at the API level, not in the PTY manager
    title = request.title if request.title else f"Terminal {pty_id[-4:]}"
    pty_info = PtyInfo.from_exxec(info, title=title)
    # Broadcast PTY updated event
    event = PtyUpdatedEvent.create(info=pty_info)
    await state.broadcast_event(event)
    return pty_info


@router.delete("/{pty_id}")
async def remove_pty(pty_id: str, state: StateDep) -> dict[str, bool]:
    """Remove/kill PTY session."""
    manager = _get_pty_manager(state)
    # Kill the PTY session
    success = await manager.kill(pty_id)
    if not success:
        raise HTTPException(status_code=404, detail="PTY session not found")
    # Cleanup session tracker
    if session := _pty_sessions.pop(pty_id, None):
        # Cancel read task
        if session.read_task and not session.read_task.done():
            session.read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.read_task

        # Close all WebSocket connections
        for ws in session.subscribers:
            with contextlib.suppress(Exception):
                await ws.close()

    # Broadcast PTY deleted event
    event = PtyDeletedEvent.create(pty_id=pty_id)
    await state.broadcast_event(event)
    return {"success": True}


@router.websocket("/{pty_id}/connect")
async def connect_pty(websocket: WebSocket, pty_id: str) -> None:
    """Connect to PTY via WebSocket for interactive terminal."""
    # Get state from websocket's app

    state: ServerState = websocket.app.state.server_state
    try:
        manager = _get_pty_manager(state)
    except HTTPException:
        # Must accept before we can close
        await websocket.accept()
        await websocket.close(code=1003, reason="PTY not supported")
        return
    except Exception as e:  # noqa: BLE001
        await websocket.accept()
        await websocket.close(code=1011, reason=f"Error: {e}")
        return

    # Check if PTY exists - if not, immediately reject like OpenCode does
    if not await manager.get_info(pty_id):
        await websocket.accept()
        await websocket.close(code=1003, reason="PTY session not found")
        return

    # PTY exists, accept the WebSocket connection
    await websocket.accept()
    # Get or create session tracker
    if pty_id not in _pty_sessions:
        _pty_sessions[pty_id] = PtySession(pty_id=pty_id)
    session = _pty_sessions[pty_id]
    session.subscribers.add(websocket)
    # Send buffered output
    if session.buffer:
        try:
            await websocket.send_text(session.buffer)
            session.buffer = ""
        except Exception:  # noqa: BLE001
            pass

    try:
        while True:
            # Receive input from client
            data = await websocket.receive_text()
            # Write to PTY stdin
            if (info := await manager.get_info(pty_id)) and info.status == "running":
                try:
                    await manager.write(pty_id, data.encode())
                except Exception:  # noqa: BLE001
                    break
            else:
                break
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        session.subscribers.discard(websocket)
