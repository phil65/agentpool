"""PTY routes (terminals in the server's execution environment)."""

import asyncio
import contextlib
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from agentpool_server.opencode_v2_server.auth import (
    AUTH_TOKEN_QUERY,
    is_allowed_origin,
    is_authorized,
)
from agentpool_server.opencode_v2_server.deps import DirectoryDep, StateDep, V2Response, ok
from agentpool_server.opencode_v2_server.errors import conflict, forbidden, pty_not_found
from agentpool_server.opencode_v2_server.location import public_ref
from agentpool_server.opencode_v2_server.ptys import TICKET_TTL, PtyLimitError, meta_frame
from opencode_v2_sdk.models import (
    PtyConnectTokenResponse,
    PtyCreateBody,
    PtyCreateResponse,
    PtyGetResponse,
    PtyListResponse,
    PtyTicketConnectToken,
    PtyUpdateBody,
    PtyUpdateResponse,
)


if TYPE_CHECKING:
    from agentpool_server.opencode_v2_server.state import ServerState


router = APIRouter(tags=["pty"], default_response_class=V2Response)

REPLAY_CHUNK = 64 * 1024
CONNECT_TOKEN_HEADER = "x-opencode-ticket"
"""OpenCode clients send ``x-opencode-ticket: 1`` when asking for a ticket. The custom header forces
a CORS preflight, so cross-origin pages cannot mint tickets."""


@router.get("/api/pty")
async def pty_list(state: StateDep, directory: DirectoryDep) -> Response:
    """PTYs opened through this server."""
    data = [s.info for s in state.ptys.sessions.values()]
    return ok(PtyListResponse(location=public_ref(directory), data=data))


@router.post("/api/pty")
async def pty_create(body: PtyCreateBody, state: StateDep, directory: DirectoryDep) -> Response:
    """Open a terminal."""
    try:
        session = await state.ptys.create(
            directory=directory,
            command=body.command,
            args=body.args,
            cwd=body.cwd,
            title=body.title,
            env=body.env,
        )
    except PtyLimitError as e:
        raise conflict(str(e), resource="pty") from None
    return ok(PtyCreateResponse(location=public_ref(directory), data=session.info))


@router.get("/api/pty/{pty_id}")
async def pty_get(pty_id: str, state: StateDep, directory: DirectoryDep) -> Response:
    """One PTY."""
    session = state.ptys.sessions.get(pty_id)
    if session is None:
        raise pty_not_found(pty_id)
    return ok(PtyGetResponse(location=public_ref(directory), data=session.info))


@router.put("/api/pty/{pty_id}")
async def pty_update(
    pty_id: str, body: PtyUpdateBody, state: StateDep, directory: DirectoryDep
) -> Response:
    """Rename or resize a PTY."""
    session = await state.ptys.update(
        pty_id,
        title=body.title,
        rows=body.size.rows if body.size else None,
        cols=body.size.cols if body.size else None,
    )
    if session is None:
        raise pty_not_found(pty_id)
    return ok(PtyUpdateResponse(location=public_ref(directory), data=session.info))


@router.delete("/api/pty/{pty_id}", status_code=204)
async def pty_remove(pty_id: str, state: StateDep) -> Response:
    """Kill a PTY."""
    if not await state.ptys.remove(pty_id):
        raise pty_not_found(pty_id)
    return Response(status_code=204)


@router.post("/api/pty/{pty_id}/connect-token")
async def pty_connect_token(
    pty_id: str, request: Request, state: StateDep, directory: DirectoryDep
) -> Response:
    """Issue a short-lived ticket for the websocket connection."""
    headers = request.headers
    if headers.get(CONNECT_TOKEN_HEADER) != "1" or not is_allowed_origin(
        headers.get("origin"), headers.get("host")
    ):
        raise forbidden("Invalid PTY connect token request")
    if pty_id not in state.ptys.sessions:
        raise pty_not_found(pty_id)
    token = PtyTicketConnectToken(ticket=state.ptys.issue_ticket(pty_id), expires_in=TICKET_TTL)
    return ok(PtyConnectTokenResponse(location=public_ref(directory), data=token))


@router.websocket("/api/pty/{pty_id}/connect")
async def pty_connect(websocket: WebSocket, pty_id: str) -> None:
    """Interactive terminal stream (replay from ``cursor``, then live output).

    Like OpenCode, the connection needs either a ticket from ``connect-token`` (browsers cannot
    set headers on a websocket) or the server password, and an allowed ``Origin``. The HTTP
    auth middleware does not see websockets, so the checks are here.
    """
    state: ServerState = websocket.app.state.v2_state
    headers = websocket.headers
    if not is_allowed_origin(headers.get("origin"), headers.get("host")):
        await websocket.close(code=4403, reason="origin not allowed")
        return
    if ticket := websocket.query_params.get("ticket"):
        if not state.ptys.consume_ticket(ticket, pty_id):
            await websocket.close(code=4403, reason="invalid ticket")
            return
    elif not is_authorized(
        headers.get("authorization"),
        websocket.query_params.get(AUTH_TOKEN_QUERY),
        websocket.app.state.v2_password,
    ):
        await websocket.close(code=4401, reason="unauthorized")
        return
    session = state.ptys.sessions.get(pty_id)
    await websocket.accept()
    if session is None:
        await websocket.close(code=4404, reason="session not found")
        return
    if session.info.status == "exited":
        await websocket.close(code=4404, reason="session exited")
        return
    raw_cursor = websocket.query_params.get("cursor")
    cursor = int(raw_cursor) if raw_cursor and raw_cursor.lstrip("-").isdigit() else None
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    replay = session.replay(cursor)
    position = session.cursor
    session.subscribers.add(queue)

    async def outbound() -> None:
        for start in range(0, len(replay), REPLAY_CHUNK):
            await websocket.send_text(replay[start : start + REPLAY_CHUNK].decode(errors="replace"))
        await websocket.send_bytes(meta_frame(position))
        while (chunk := await queue.get()) is not None:
            await websocket.send_text(chunk.decode(errors="replace"))
        await websocket.close(code=1000)

    sender = asyncio.create_task(outbound())
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (text := message.get("text")) is not None:
                await state.ptys.write(session, text.encode())
            elif (data := message.get("bytes")) is not None:
                with contextlib.suppress(UnicodeDecodeError):
                    await state.ptys.write(session, data.decode().encode())
    except WebSocketDisconnect:
        pass
    finally:
        session.subscribers.discard(queue)
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sender
