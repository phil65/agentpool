"""A v2 client connection behind the v1 ``Client`` interface the session uses.

The session core (``ACPSession``, its input provider, slash commands, the forwarding
of nested ACP agents' permission requests) talks to a v1 ``Client``. A v2 session
gets this adapter as its client:

- ``session/update``: the v1 update is translated (``v2_translate``); updates
  without a v2 form (``current_mode_update``) are left out, the v2 output sends
  config options instead.
- ``session/request_permission``: the tool call becomes the v2 request's
  ``subject``, its title the required ``title``; ``allowed`` / ``denied`` outcomes
  map to ``selected`` / ``cancelled``.
- ``elicitation/create`` and ``elicitation/complete``: v1 already has v2's shape.
- ``fs/*`` and ``terminal/*``: v2 has neither (tools run on the agent's host, see
  ``ACP_V2_SERVER.md``); a v2 session never advertises them, so they are not called.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, assert_never

from aclientp.exceptions import RequestError
from aclientp.schema import (
    AuthStatusUpdateNotification,
    RequestPermissionResponse,
    v2,
)
from aclientp.schema.elicitation import ElicitationResponse

from agentpool.log import get_logger
from agentpool_server.acp_server.v2_translate import UpdateTranslator, tool_call_update


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from aclientp.schema import (
        CreateTerminalRequest,
        CreateTerminalResponse,
        KillTerminalRequest,
        KillTerminalResponse,
        MessageMcpNotification,
        MessageMcpRequest,
        MessageMcpResponse,
        ReadTextFileRequest,
        ReadTextFileResponse,
        ReleaseTerminalRequest,
        ReleaseTerminalResponse,
        RequestPermissionRequest,
        SessionNotification,
        TerminalOutputRequest,
        TerminalOutputResponse,
        WaitForTerminalExitRequest,
        WaitForTerminalExitResponse,
        WriteTextFileRequest,
        WriteTextFileResponse,
    )
    from aclientp.schema.elicitation import ElicitationCompleteNotification, ElicitationRequest
    from aclientp.v2.agent import AgentSideConnection as V2AgentSideConnection


logger = get_logger(__name__)


def permission_request(params: RequestPermissionRequest) -> v2.RequestPermissionRequest:
    """A v1 permission request in v2."""
    tool_call = params.tool_call
    return v2.RequestPermissionRequest(
        session_id=params.session_id,
        title=tool_call.title or tool_call.name or "Permission required",
        subject=v2.ToolCallPermissionSubject(
            tool_call=tool_call_update(tool_call, v2.ToolCallUpdate)
        ),
        options=[
            v2.PermissionOption(option_id=o.option_id, name=o.name, kind=o.kind)
            for o in params.options
        ],
    )


def permission_response(response: v2.RequestPermissionResponse) -> RequestPermissionResponse:
    """A v2 permission answer in v1; anything but a selection denies."""
    match response.outcome:
        case v2.SelectedPermissionOutcome(option_id=option_id):
            return RequestPermissionResponse.allowed(option_id)
        case v2.CancelledPermissionOutcome() | v2.UnknownPermissionOutcome():
            return RequestPermissionResponse.denied()
        case _ as unreachable:
            assert_never(unreachable)


def elicitation_request(params: ElicitationRequest) -> v2.CreateElicitationRequest:
    """A v1 elicitation request in v2 (same flat shape, discriminated by ``mode``)."""
    wire = params.model_dump(by_alias=True, exclude_none=True)
    match params.mode:
        case "form":
            return v2.FormElicitationRequest.model_validate(wire)
        case "url":
            return v2.UrlElicitationRequest.model_validate(wire)
        case mode:  # Modes are open-ended; agentpool only creates form and url ones
            raise RequestError.invalid_params({"details": f"unknown elicitation mode {mode!r}"})


class V2ClientAdapter:
    """The v1 ``Client`` of a session whose client speaks ACP v2."""

    def __init__(self, conn: V2AgentSideConnection, translator: UpdateTranslator) -> None:
        """Wrap a v2 connection.

        Args:
            conn: The connection to the v2 client
            translator: Translates the session's updates (shared with its turns)
        """
        self.conn = conn
        self.translator = translator
        self._waiting: dict[str, int] = {}
        """Requests waiting for the user, per session."""

    @asynccontextmanager
    async def _awaiting_user(self, session_id: str | None) -> AsyncGenerator[None]:
        """The session ``requires_action`` while its first to last request waits for the user.

        ``running`` again once none waits: the request came from the session's turn.
        """
        if session_id is None:
            yield
            return
        self._waiting[session_id] = self._waiting.get(session_id, 0) + 1
        if self._waiting[session_id] == 1:
            await self._send_state(session_id, v2.RequiresActionStateSessionUpdate())
        try:
            yield
        finally:
            self._waiting[session_id] -= 1
            if not self._waiting[session_id]:
                del self._waiting[session_id]
                await self._send_state(session_id, v2.RunningStateSessionUpdate())

    async def _send_state(self, session_id: str, state: v2.StateSessionUpdate) -> None:
        notification = v2.UpdateSessionNotification(session_id=session_id, update=state)
        await self.conn.session_update(notification)

    async def session_update(self, params: SessionNotification) -> None:
        if (update := self.translator.translate(params.update)) is None:
            return
        notification = v2.UpdateSessionNotification(session_id=params.session_id, update=update)
        await self.conn.session_update(notification)

    async def request_permission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse:
        async with self._awaiting_user(params.session_id):
            response = await self.conn.request_permission(permission_request(params))
        return permission_response(response)

    async def elicitation(self, params: ElicitationRequest) -> ElicitationResponse:
        async with self._awaiting_user(params.session_id):
            response = await self.conn.create_elicitation(elicitation_request(params))
        return ElicitationResponse.model_validate(v2.to_wire(response))

    async def elicitation_complete(self, params: ElicitationCompleteNotification) -> None:
        notification = v2.CompleteElicitationNotification(elicitation_id=params.elicitation_id)
        await self.conn.complete_elicitation(notification)

    async def auth_status_update(self, params: AuthStatusUpdateNotification) -> None:
        wire = params.model_dump(by_alias=True, exclude_none=True)
        await self.conn.auth_status_update(v2.AuthStatusUpdateNotification.model_validate(wire))

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        result = await self.conn.ext_method(method, params)
        if not isinstance(result, dict):
            msg = f"{method} answered with {type(result).__name__}, not an object"
            raise RequestError.internal_error({"details": msg})
        return result

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        await self.conn.ext_notification(method, params)

    # v2 has no client filesystem or terminals; a v2 session never advertises them

    async def write_text_file(self, params: WriteTextFileRequest) -> WriteTextFileResponse | None:
        raise RequestError.method_not_found("fs/write_text_file")

    async def read_text_file(self, params: ReadTextFileRequest) -> ReadTextFileResponse:
        raise RequestError.method_not_found("fs/read_text_file")

    async def create_terminal(self, params: CreateTerminalRequest) -> CreateTerminalResponse:
        raise RequestError.method_not_found("terminal/create")

    async def terminal_output(self, params: TerminalOutputRequest) -> TerminalOutputResponse:
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(
        self, params: ReleaseTerminalRequest
    ) -> ReleaseTerminalResponse | None:
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(
        self, params: WaitForTerminalExitRequest
    ) -> WaitForTerminalExitResponse:
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(self, params: KillTerminalRequest) -> KillTerminalResponse | None:
        raise RequestError.method_not_found("terminal/kill")

    # MCP over ACP (``acp`` MCP servers) is not advertised

    async def mcp_message(self, params: MessageMcpRequest) -> MessageMcpResponse:
        raise RequestError.method_not_found("mcp/message")

    async def mcp_notification(self, params: MessageMcpNotification) -> None:
        return
