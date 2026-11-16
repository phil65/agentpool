"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, cast

from fastapi import Depends, Request


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


def get_state(request: Request) -> ServerState:
    """Get server state from request.

    The state is stored on app.state.server_state during app creation.
    """
    from agentpool_server.opencode_server.state import ServerState

    return cast(ServerState, request.app.state.server_state)


StateDep = Annotated["ServerState", Depends(get_state)]
