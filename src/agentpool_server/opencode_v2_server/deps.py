"""FastAPI dependencies and router helpers of the V2 server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import unquote

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from agentpool_server.opencode_v2_server.errors import session_not_found
from agentpool_server.opencode_v2_server.events import dump_model
from agentpool_server.opencode_v2_server.location import normalize_directory
from agentpool_server.opencode_v2_server.session import V2Session
from agentpool_server.opencode_v2_server.state import ServerState, SessionNotFoundError


if TYPE_CHECKING:
    from pydantic import BaseModel


def get_state(request: Request) -> ServerState:
    """The server state stored on the app."""
    state: ServerState = request.app.state.v2_state
    return state


StateDep = Annotated[ServerState, Depends(get_state)]


def get_directory(request: Request, state: StateDep) -> str:
    """The request's location directory.

    Resolved like OpenCode: ``location[directory]`` query parameter, then the
    ``x-opencode-directory`` header, then the server's working directory.
    """
    directory = request.query_params.get("location[directory]") or request.query_params.get(
        "directory"
    )
    if not directory and (header := request.headers.get("x-opencode-directory")):
        directory = unquote(header)
    return normalize_directory(directory) if directory else state.working_dir


DirectoryDep = Annotated[str, Depends(get_directory)]


async def get_session(session_id: str, state: StateDep) -> V2Session:
    """The session addressed by the ``sessionID`` path parameter.

    Raises:
        ApiError: SessionNotFoundError (404)
    """
    try:
        return await state.get_session(session_id)
    except SessionNotFoundError:
        raise session_not_found(session_id) from None


SessionDep = Annotated[V2Session, Depends(get_session)]


class V2Response(JSONResponse):
    """JSON response serializing protocol models with aliases and without nulls."""

    def render(self, content: Any) -> bytes:
        """Render content (protocol models are dumped by alias, nulls dropped)."""
        from fastapi.encoders import jsonable_encoder

        return super().render(jsonable_encoder(content, by_alias=True, exclude_none=True))


def ok(model: BaseModel) -> V2Response:
    """Response for a protocol model."""
    return V2Response(dump_model(model))
