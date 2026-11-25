"""Server-level routes: info, location, events, projects and configuration."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
import shutil
import socket
import tempfile
from typing import TYPE_CHECKING

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse

from agentpool.log import get_logger
from agentpool_server.opencode_v2_server.bus import SubscriberOverflowError, sse_frame
from agentpool_server.opencode_v2_server.deps import DirectoryDep, StateDep, V2Response, ok
from agentpool_server.opencode_v2_server.errors import location_not_found, unavailable
from agentpool_server.opencode_v2_server.events import dump_model, new_event_id
from agentpool_server.opencode_v2_server.location import public_ref
from opencode_v2_sdk._version import OPENCODE_VERSION
from opencode_v2_sdk.models import (
    ConfigShellOption,
    ProjectUpdateBody,
    ServerInfo,
    ServerInfoCapabilitiesVariant0,
    ServerInfoPaths,
    V2EventServerConnected,
)


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


logger = get_logger(__name__)

router = APIRouter(tags=["server"], default_response_class=V2Response)

SHELL_CANDIDATES = ("bash", "zsh", "fish", "sh", "nu", "pwsh")


@router.get("/api/info")
async def server_info(state: StateDep) -> Response:
    """Server identity and capabilities (also used as health check by clients)."""
    host = state.host
    urls = [f"http://{host}:{state.port}"] if state.port else []
    info = ServerInfo(
        version=OPENCODE_VERSION,
        pid=os.getpid(),
        hostname=socket.gethostname(),
        urls=urls,
        paths=ServerInfoPaths(tmp=tempfile.gettempdir()),
        capabilities=ServerInfoCapabilitiesVariant0(persistent_pty=False),
    )
    return ok(info)


@router.post("/api/pair")
async def server_pair() -> Response:
    """Pairing codes are not supported (use ``OPENCODE_PASSWORD``)."""
    raise unavailable("pairing")


@router.get("/auth/connect/{code}")
async def server_connect(code: str) -> Response:
    """Pairing codes are not supported (use ``OPENCODE_PASSWORD``)."""
    raise unavailable("pairing")


@router.get("/api/location")
async def location_get(state: StateDep, directory: DirectoryDep) -> Response:
    """Resolve the request's location (directory and project)."""
    if not Path(directory).is_dir():
        raise location_not_found(directory)
    return ok(await state.projects.location_info(directory))


@router.post("/api/location/reload", status_code=204)
async def location_reload(state: StateDep) -> Response:
    """Reload configuration: drops cached model discovery."""
    state.catalog.invalidate()
    return Response(status_code=204)


@router.get("/api/event")
async def event_subscribe(
    state: StateDep,
    types: list[str] | None = Query(default=None),  # noqa: B008
) -> StreamingResponse:
    """Volatile live event stream (SSE)."""
    connected = V2EventServerConnected(id=new_event_id(), data={})
    stream = state.bus.subscribe(frozenset(types) if types else None, initial=sse_frame(connected))
    return StreamingResponse(
        _with_heartbeat(stream),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _with_heartbeat(
    stream: AsyncGenerator[str], interval: float = 15.0
) -> AsyncGenerator[str]:
    """Interleave SSE comment heartbeats so idle connections stay open."""
    iterator = stream
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield ": heartbeat\n\n"
                continue
            finished, pending = pending, None
            try:
                frame = finished.result()
            except StopAsyncIteration:
                return
            except SubscriberOverflowError:
                # A client too slow to keep up: end its stream cleanly, it reconnects
                logger.warning("SSE subscriber overflowed, closing its stream")
                return
            yield frame
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pending
        await iterator.aclose()


@router.get("/api/project")
async def project_list(state: StateDep, directory: DirectoryDep) -> Response:
    """Known projects."""
    await state.projects.resolve(directory)
    return _json([dump_model(p) for p in state.projects.projects()])


@router.patch("/api/project/{project_id}")
async def project_update(project_id: str, body: ProjectUpdateBody, state: StateDep) -> Response:
    """Rename a project."""
    state.projects.rename(project_id, body.name)
    for project in state.projects.projects():
        if project.id == project_id:
            return ok(project)
    raise unavailable("project", f"Project not found: {project_id}")


@router.get("/api/config")
async def config_get() -> Response:
    """Configuration entries (AgentPool is configured via its YAML manifest)."""
    return _json([])


@router.get("/api/config/shell")
async def config_shells() -> Response:
    """Shells available on this machine."""
    current = os.environ.get("SHELL", "")
    options = [
        ConfigShellOption(path=path, name=name, acceptable=True)
        for name in SHELL_CANDIDATES
        if (path := shutil.which(name))
    ]
    if current and not any(o.path == current for o in options):
        options.insert(0, ConfigShellOption(path=current, name=Path(current).name, acceptable=True))
    return _json([dump_model(o) for o in options])


@router.patch("/api/experimental/config", status_code=204)
async def config_update() -> Response:
    """Global configuration is defined by the AgentPool manifest and can't be edited here."""
    raise unavailable("config", "Configuration is managed through the AgentPool manifest")


def location_ref(directory: str) -> dict[str, str]:
    """Serialized ``Location.PublicRef``."""
    return dump_model(public_ref(directory))


def _json(content: object) -> Response:
    return JSONResponse(content)
