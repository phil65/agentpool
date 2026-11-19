"""**UNSTABLE**: Agent side of the draft ACP v2 protocol.

Subclass :class:`Agent` and override the methods the agent supports; every other
request is answered with ``-32601`` (Method not found), and other notifications are
ignored. :class:`AgentSideConnection` runs the JSON-RPC connection, enforces the v2
initialization rules and offers typed calls to the client.

Mirrors the agent bindings of the pinned TypeScript SDK (``schema/acp/references.toml``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

from acp.connection import Connection
from acp.exceptions import RequestError
from acp.schema.v2.base import to_wire
from acp.schema.v2.common import Implementation
from acp.schema.v2.initialize import InitializeRequest
from acp.schema.v2.protocol import AGENT_NOTIFICATIONS, AGENT_REQUESTS, CLIENT_REQUESTS


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from anyio.abc import ByteReceiveStream, ByteSendStream

    from acp.connection import StreamObserver
    from acp.schema.v2 import (
        ArchiveSessionRequest,
        ArchiveSessionResponse,
        AuthStatusRequest,
        AuthStatusResponse,
        AuthStatusUpdateNotification,
        CancelSessionNotification,
        CancelTerminalRequest,
        CancelTerminalResponse,
        CancelToolCallRequest,
        CancelToolCallResponse,
        CloseSessionRequest,
        CloseSessionResponse,
        CompleteElicitationNotification,
        CreateElicitationRequest,
        CreateElicitationResponse,
        DeleteSessionRequest,
        DeleteSessionResponse,
        DisableProviderRequest,
        DisableProviderResponse,
        ForkSessionRequest,
        ForkSessionResponse,
        InitializeResponse,
        ListProvidersRequest,
        ListProvidersResponse,
        ListSessionsRequest,
        ListSessionsResponse,
        LoginAuthRequest,
        LoginAuthResponse,
        LogoutAuthRequest,
        LogoutAuthResponse,
        MessageMcpNotification,
        MessageMcpRequest,
        MessageMcpResponse,
        NewSessionRequest,
        NewSessionResponse,
        PromptRequest,
        PromptResponse,
        RequestPermissionRequest,
        RequestPermissionResponse,
        ResumeSessionRequest,
        ResumeSessionResponse,
        SessionOptionsRequest,
        SessionOptionsResponse,
        SessionStatusRequest,
        SessionStatusResponse,
        SetProviderRequest,
        SetProviderResponse,
        SetSessionConfigOptionRequest,
        SetSessionConfigOptionResponse,
        SetSessionTitleRequest,
        SetSessionTitleResponse,
        UnarchiveSessionRequest,
        UnarchiveSessionResponse,
        UpdateSessionNotification,
    )


PROTOCOL_VERSION = 2
"""The protocol version this module implements."""

_UNKNOWN_IMPLEMENTATION = {"name": "unknown", "version": "unknown"}
"""``info`` substituted when a request for a newer version has a malformed one."""


class Agent:
    """Base class of a v2 agent. Override the methods the agent supports."""

    # --- Requests -------------------------------------------------------------------

    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        """Handle ``initialize``; ``protocol_version`` of ``params`` is always 2."""
        raise RequestError.method_not_found("initialize")

    async def login(self, params: LoginAuthRequest) -> LoginAuthResponse:
        """Handle ``auth/login``."""
        raise RequestError.method_not_found("auth/login")

    async def logout(self, params: LogoutAuthRequest) -> LogoutAuthResponse:
        """Handle ``auth/logout``."""
        raise RequestError.method_not_found("auth/logout")

    async def auth_status(self, params: AuthStatusRequest) -> AuthStatusResponse:
        """Handle ``auth/status``."""
        raise RequestError.method_not_found("auth/status")

    async def list_providers(self, params: ListProvidersRequest) -> ListProvidersResponse:
        """Handle ``providers/list``."""
        raise RequestError.method_not_found("providers/list")

    async def set_provider(self, params: SetProviderRequest) -> SetProviderResponse:
        """Handle ``providers/set``."""
        raise RequestError.method_not_found("providers/set")

    async def disable_provider(self, params: DisableProviderRequest) -> DisableProviderResponse:
        """Handle ``providers/disable``."""
        raise RequestError.method_not_found("providers/disable")

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        """Handle ``session/new``."""
        raise RequestError.method_not_found("session/new")

    async def resume_session(self, params: ResumeSessionRequest) -> ResumeSessionResponse:
        """Handle ``session/resume`` (send ``replay_complete`` after a requested replay)."""
        raise RequestError.method_not_found("session/resume")

    async def fork_session(self, params: ForkSessionRequest) -> ForkSessionResponse:
        """Handle ``session/fork``."""
        raise RequestError.method_not_found("session/fork")

    async def close_session(self, params: CloseSessionRequest) -> CloseSessionResponse:
        """Handle ``session/close``."""
        raise RequestError.method_not_found("session/close")

    async def list_sessions(self, params: ListSessionsRequest) -> ListSessionsResponse:
        """Handle ``session/list``."""
        raise RequestError.method_not_found("session/list")

    async def delete_session(self, params: DeleteSessionRequest) -> DeleteSessionResponse:
        """Handle ``session/delete``."""
        raise RequestError.method_not_found("session/delete")

    async def archive_session(self, params: ArchiveSessionRequest) -> ArchiveSessionResponse:
        """Handle ``session/archive``."""
        raise RequestError.method_not_found("session/archive")

    async def unarchive_session(self, params: UnarchiveSessionRequest) -> UnarchiveSessionResponse:
        """Handle ``session/unarchive``."""
        raise RequestError.method_not_found("session/unarchive")

    async def session_status(self, params: SessionStatusRequest) -> SessionStatusResponse:
        """Handle ``session/status``."""
        raise RequestError.method_not_found("session/status")

    async def session_options(self, params: SessionOptionsRequest) -> SessionOptionsResponse:
        """Handle ``session/options``."""
        raise RequestError.method_not_found("session/options")

    async def set_session_title(self, params: SetSessionTitleRequest) -> SetSessionTitleResponse:
        """Handle ``session/set_title``."""
        raise RequestError.method_not_found("session/set_title")

    async def set_session_config_option(
        self, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse:
        """Handle ``session/set_config_option``."""
        raise RequestError.method_not_found("session/set_config_option")

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        """Handle ``session/prompt``: insert the message and return its ID right away.

        Report the turn with ``state_update`` notifications; do not wait for it here.
        """
        raise RequestError.method_not_found("session/prompt")

    async def cancel_tool_call(self, params: CancelToolCallRequest) -> CancelToolCallResponse:
        """Handle ``tool_call/cancel``."""
        raise RequestError.method_not_found("tool_call/cancel")

    async def cancel_terminal(self, params: CancelTerminalRequest) -> CancelTerminalResponse:
        """Handle ``terminal/cancel``."""
        raise RequestError.method_not_found("terminal/cancel")

    async def mcp_message(self, params: MessageMcpRequest) -> MessageMcpResponse:
        """Handle an ``mcp/message`` request."""
        raise RequestError.method_not_found("mcp/message")

    async def ext_method(self, method: str, params: Any) -> Any:
        """Handle a custom (``_``-prefixed) request."""
        raise RequestError.method_not_found(method)

    # --- Notifications ----------------------------------------------------------------

    async def cancel(self, params: CancelSessionNotification) -> None:
        """Handle ``session/cancel``: stop foreground work and not-yet-inserted prompts."""

    async def mcp_notification(self, params: MessageMcpNotification) -> None:
        """Handle an ``mcp/message`` notification."""

    async def ext_notification(self, method: str, params: Any) -> None:
        """Handle a custom (``_``-prefixed) notification."""


def _request_handlers(agent: Agent) -> dict[str, Callable[[Any], Awaitable[BaseModel]]]:
    return {
        "initialize": agent.initialize,
        "auth/login": agent.login,
        "auth/logout": agent.logout,
        "auth/status": agent.auth_status,
        "providers/list": agent.list_providers,
        "providers/set": agent.set_provider,
        "providers/disable": agent.disable_provider,
        "session/new": agent.new_session,
        "session/resume": agent.resume_session,
        "session/fork": agent.fork_session,
        "session/close": agent.close_session,
        "session/list": agent.list_sessions,
        "session/delete": agent.delete_session,
        "session/archive": agent.archive_session,
        "session/unarchive": agent.unarchive_session,
        "session/status": agent.session_status,
        "session/options": agent.session_options,
        "session/set_title": agent.set_session_title,
        "session/set_config_option": agent.set_session_config_option,
        "session/prompt": agent.prompt,
        "tool_call/cancel": agent.cancel_tool_call,
        "terminal/cancel": agent.cancel_terminal,
        "mcp/message": agent.mcp_message,
    }


def _notification_handlers(agent: Agent) -> dict[str, Callable[[Any], Awaitable[None]]]:
    return {
        "session/cancel": agent.cancel,
        "mcp/message": agent.mcp_notification,
    }


InitializationPhase = Literal["uninitialized", "initializing", "initialized"]


def parse_initialize(params: Any) -> InitializeRequest:
    """Parse ``initialize`` params as v2.

    A version below 2 is rejected with ``-32022``. A request for a newer version was
    written for a schema this endpoint does not know, so it is read leniently (a
    malformed ``info`` becomes ``unknown``) and answered as v2.
    """
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    if not isinstance(requested, int) or isinstance(requested, bool):
        raise RequestError.invalid_params({"protocolVersion": requested})
    if requested < PROTOCOL_VERSION:
        raise RequestError.unsupported_protocol_version(requested, [PROTOCOL_VERSION])
    assert isinstance(params, dict)
    data = {**params, "protocolVersion": PROTOCOL_VERSION}
    if requested > PROTOCOL_VERSION:
        try:
            Implementation.model_validate(data.get("info"))
        except ValidationError:
            data["info"] = _UNKNOWN_IMPLEMENTATION
    return InitializeRequest.model_validate(data)


class AgentSideConnection:
    """**UNSTABLE**: Connection of a v2 agent to a client.

    Enforces the v2 rules: requests before ``initialize`` get ``-32600`` and
    notifications are ignored, ``initialize`` may only be repeated before other
    requests, request IDs must be strings or integers, and batches get ``-32600``.
    """

    PROTOCOL_VERSION = PROTOCOL_VERSION
    """The protocol version this connection speaks."""

    def __init__(
        self,
        to_agent: Callable[[AgentSideConnection], Agent] | Agent,
        input_stream: ByteSendStream,
        output_stream: ByteReceiveStream,
        observers: list[StreamObserver] | None = None,
    ) -> None:
        """Start the connection.

        Args:
            to_agent: The agent, or a factory receiving this connection.
            input_stream: Stream to the client.
            output_stream: Stream from the client.
            observers: Observers of the raw messages.
        """
        self._agent = to_agent if isinstance(to_agent, Agent) else to_agent(self)
        self._requests = _request_handlers(self._agent)
        self._notifications = _notification_handlers(self._agent)
        self.phase: InitializationPhase = "uninitialized"
        self.initialize_request: InitializeRequest | None = None
        self._other_request_seen = False
        self._conn = Connection(
            self.handle,
            input_stream,
            output_stream,
            observers=observers,
            reject_batches=True,
            strict_request_ids=True,
        )

    @classmethod
    def on_connection(
        cls,
        connection: Connection,
        to_agent: Callable[[AgentSideConnection], Agent] | Agent,
    ) -> AgentSideConnection:
        """Serve v2 on an existing connection whose handler delegates to :meth:`handle`.

        Used by the version router, which owns the connection until ``initialize``.
        """
        self = cls.__new__(cls)
        self._agent = to_agent if isinstance(to_agent, Agent) else to_agent(self)
        self._requests = _request_handlers(self._agent)
        self._notifications = _notification_handlers(self._agent)
        self.phase = "uninitialized"
        self.initialize_request = None
        self._other_request_seen = False
        connection.reject_batches = True
        connection.strict_request_ids = True
        self._conn = connection
        return self

    @property
    def connection(self) -> Connection:
        """The underlying JSON-RPC connection."""
        return self._conn

    async def handle(self, method: str, params: Any, is_notification: bool) -> Any:
        """Handle one incoming message (the connection's handler)."""
        if is_notification:
            await self._handle_notification(method, params)
            return None
        if method == "initialize":
            return await self._initialize(params)
        if self.phase != "initialized":
            details = f"ACP v2 connection must be initialized before {method!r}"
            raise RequestError.invalid_request({"details": details})
        self._other_request_seen = True
        if method.startswith("_"):
            return await self._agent.ext_method(method, params)
        if (handler := self._requests.get(method)) is None:
            raise RequestError.method_not_found(method)
        parsed = AGENT_REQUESTS[method].params.validate_python(params or {})
        return to_wire(await handler(parsed))

    async def _initialize(self, params: Any) -> dict[str, Any]:
        if self.phase == "initializing":
            raise RequestError.invalid_request({"details": "initialize is already in progress"})
        if self.phase == "initialized" and self._other_request_seen:
            details = "initialize must not be repeated after other requests"
            raise RequestError.invalid_request({"details": details})
        request = parse_initialize(params)
        previous = self.phase
        self.phase = "initializing"
        try:
            response = await self._agent.initialize(request)
        except BaseException:
            self.phase = previous
            raise
        if response.protocol_version != PROTOCOL_VERSION:
            self.phase = previous
            raise RequestError.internal_error({"details": "agent answered a non-v2 version"})
        self.initialize_request = request
        self.phase = "initialized"
        return to_wire(response)

    async def _handle_notification(self, method: str, params: Any) -> None:
        if self.phase != "initialized":
            return  # ignored before initialization, as v2 requires
        if method.startswith("_"):
            await self._agent.ext_notification(method, params)
            return
        if (handler := self._notifications.get(method)) is None:
            return
        await handler(AGENT_NOTIFICATIONS[method].validate_python(params or {}))

    # --- Calls to the client --------------------------------------------------------

    async def _request(self, method: str, params: BaseModel) -> Any:
        result = await self._conn.send_request(method, to_wire(params))
        return CLIENT_REQUESTS[method].result.validate_python(result)

    async def session_update(self, params: UpdateSessionNotification) -> None:
        """Send a ``session/update`` notification."""
        await self._conn.send_notification("session/update", to_wire(params))

    async def request_permission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse:
        """Ask the user for permission (``session/request_permission``)."""
        response: RequestPermissionResponse = await self._request(
            "session/request_permission", params
        )
        return response

    async def create_elicitation(
        self, params: CreateElicitationRequest
    ) -> CreateElicitationResponse:
        """Ask the user for structured input (``elicitation/create``)."""
        response: CreateElicitationResponse = await self._request("elicitation/create", params)
        return response

    async def complete_elicitation(self, params: CompleteElicitationNotification) -> None:
        """Report that a URL elicitation is complete (``elicitation/complete``)."""
        await self._conn.send_notification("elicitation/complete", to_wire(params))

    async def auth_status_update(self, params: AuthStatusUpdateNotification) -> None:
        """Report a change of the authentication status (``auth/status_update``)."""
        await self._conn.send_notification("auth/status_update", to_wire(params))

    async def mcp_message(self, params: MessageMcpRequest) -> MessageMcpResponse:
        """Send an ``mcp/message`` request to the client."""
        response: MessageMcpResponse = await self._request("mcp/message", params)
        return response

    async def mcp_notification(self, params: MessageMcpNotification) -> None:
        """Send an ``mcp/message`` notification to the client."""
        await self._conn.send_notification("mcp/message", to_wire(params))

    async def ext_method(self, method: str, params: Any) -> Any:
        """Send a custom (``_``-prefixed) request."""
        return await self._conn.send_request(method, params)

    async def ext_notification(self, method: str, params: Any) -> None:
        """Send a custom (``_``-prefixed) notification."""
        await self._conn.send_notification(method, params)

    async def close(self) -> None:
        """Close the connection."""
        await self._conn.close()

    async def wait_closed(self) -> None:
        """Wait until the client disconnected or the connection was closed."""
        await self._conn.wait_closed()
