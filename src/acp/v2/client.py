"""**UNSTABLE**: Client side of the draft ACP v2 protocol.

Subclass :class:`Client` and override the methods the client supports; every other
request from the agent is answered with ``-32601`` (Method not found), and other
notifications are ignored. :class:`ClientSideConnection` runs the JSON-RPC connection
and offers typed calls for every agent method.

Mirrors the client bindings of the pinned TypeScript SDK (``schema/acp/references.toml``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from acp.connection import Connection
from acp.exceptions import RequestError
from acp.schema.v2.base import to_wire
from acp.schema.v2.protocol import AGENT_REQUESTS, CLIENT_NOTIFICATIONS, CLIENT_REQUESTS
from acp.v2.agent import PROTOCOL_VERSION


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from anyio.abc import ByteReceiveStream, ByteSendStream
    from pydantic import BaseModel

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
        InitializeRequest,
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


class Client:
    """Base class of a v2 client. Override the methods the client supports."""

    async def request_permission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse:
        """Handle ``session/request_permission``."""
        raise RequestError.method_not_found("session/request_permission")

    async def create_elicitation(
        self, params: CreateElicitationRequest
    ) -> CreateElicitationResponse:
        """Handle ``elicitation/create``."""
        raise RequestError.method_not_found("elicitation/create")

    async def mcp_message(self, params: MessageMcpRequest) -> MessageMcpResponse:
        """Handle an ``mcp/message`` request."""
        raise RequestError.method_not_found("mcp/message")

    async def ext_method(self, method: str, params: Any) -> Any:
        """Handle a custom (``_``-prefixed) request."""
        raise RequestError.method_not_found(method)

    async def session_update(self, params: UpdateSessionNotification) -> None:
        """Handle a ``session/update`` notification."""

    async def complete_elicitation(self, params: CompleteElicitationNotification) -> None:
        """Handle ``elicitation/complete``."""

    async def auth_status_update(self, params: AuthStatusUpdateNotification) -> None:
        """Handle ``auth/status_update``."""

    async def mcp_notification(self, params: MessageMcpNotification) -> None:
        """Handle an ``mcp/message`` notification."""

    async def ext_notification(self, method: str, params: Any) -> None:
        """Handle a custom (``_``-prefixed) notification."""


def _request_handlers(client: Client) -> dict[str, Callable[[Any], Awaitable[BaseModel]]]:
    return {
        "session/request_permission": client.request_permission,
        "elicitation/create": client.create_elicitation,
        "mcp/message": client.mcp_message,
    }


def _notification_handlers(client: Client) -> dict[str, Callable[[Any], Awaitable[None]]]:
    return {
        "session/update": client.session_update,
        "elicitation/complete": client.complete_elicitation,
        "auth/status_update": client.auth_status_update,
        "mcp/message": client.mcp_notification,
    }


class ClientSideConnection:
    """**UNSTABLE**: Connection of a v2 client to an agent.

    Requests from the agent are answered with ``-32600`` until ``initialize`` has
    completed; earlier notifications are ignored.
    """

    def __init__(
        self,
        to_client: Callable[[ClientSideConnection], Client] | Client,
        input_stream: ByteSendStream,
        output_stream: ByteReceiveStream,
        observers: list[StreamObserver] | None = None,
    ) -> None:
        """Start the connection.

        Args:
            to_client: The client, or a factory receiving this connection.
            input_stream: Stream to the agent.
            output_stream: Stream from the agent.
            observers: Observers of the raw messages.
        """
        self._client = to_client if isinstance(to_client, Client) else to_client(self)
        self._requests = _request_handlers(self._client)
        self._notifications = _notification_handlers(self._client)
        self.initialize_response: InitializeResponse | None = None
        self._conn = Connection(
            self.handle,
            input_stream,
            output_stream,
            observers=observers,
            reject_batches=True,
            strict_request_ids=True,
        )

    @property
    def connection(self) -> Connection:
        """The underlying JSON-RPC connection."""
        return self._conn

    async def handle(self, method: str, params: Any, is_notification: bool) -> Any:
        """Handle one incoming message (the connection's handler)."""
        initialized = self.initialize_response is not None
        if is_notification:
            if not initialized:
                return None
            if method.startswith("_"):
                await self._client.ext_notification(method, params)
            elif handler := self._notifications.get(method):
                await handler(CLIENT_NOTIFICATIONS[method].validate_python(params or {}))
            return None
        if not initialized:
            details = f"ACP v2 connection must be initialized before {method!r}"
            raise RequestError.invalid_request({"details": details})
        if method.startswith("_"):
            return await self._client.ext_method(method, params)
        if (request_handler := self._requests.get(method)) is None:
            raise RequestError.method_not_found(method)
        parsed = CLIENT_REQUESTS[method].params.validate_python(params or {})
        return to_wire(await request_handler(parsed))

    async def _request(self, method: str, params: BaseModel) -> Any:
        result = await self._conn.send_request(method, to_wire(params))
        return AGENT_REQUESTS[method].result.validate_python(result)

    # --- Calls to the agent ---------------------------------------------------------

    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        """Send ``initialize``; the agent must answer with protocol version 2."""
        response: InitializeResponse = await self._request("initialize", params)
        if response.protocol_version != PROTOCOL_VERSION:
            data = {"expected": PROTOCOL_VERSION, "received": response.protocol_version}
            raise RequestError.invalid_request(data)
        self.initialize_response = response
        return response

    async def login(self, params: LoginAuthRequest) -> LoginAuthResponse:
        """Send ``auth/login``."""
        response: LoginAuthResponse = await self._request("auth/login", params)
        return response

    async def logout(self, params: LogoutAuthRequest) -> LogoutAuthResponse:
        """Send ``auth/logout``."""
        response: LogoutAuthResponse = await self._request("auth/logout", params)
        return response

    async def auth_status(self, params: AuthStatusRequest) -> AuthStatusResponse:
        """Send ``auth/status``."""
        response: AuthStatusResponse = await self._request("auth/status", params)
        return response

    async def list_providers(self, params: ListProvidersRequest) -> ListProvidersResponse:
        """Send ``providers/list``."""
        response: ListProvidersResponse = await self._request("providers/list", params)
        return response

    async def set_provider(self, params: SetProviderRequest) -> SetProviderResponse:
        """Send ``providers/set``."""
        response: SetProviderResponse = await self._request("providers/set", params)
        return response

    async def disable_provider(self, params: DisableProviderRequest) -> DisableProviderResponse:
        """Send ``providers/disable``."""
        response: DisableProviderResponse = await self._request("providers/disable", params)
        return response

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        """Send ``session/new``."""
        response: NewSessionResponse = await self._request("session/new", params)
        return response

    async def resume_session(self, params: ResumeSessionRequest) -> ResumeSessionResponse:
        """Send ``session/resume``."""
        response: ResumeSessionResponse = await self._request("session/resume", params)
        return response

    async def fork_session(self, params: ForkSessionRequest) -> ForkSessionResponse:
        """Send ``session/fork``."""
        response: ForkSessionResponse = await self._request("session/fork", params)
        return response

    async def close_session(self, params: CloseSessionRequest) -> CloseSessionResponse:
        """Send ``session/close``."""
        response: CloseSessionResponse = await self._request("session/close", params)
        return response

    async def list_sessions(self, params: ListSessionsRequest) -> ListSessionsResponse:
        """Send ``session/list``."""
        response: ListSessionsResponse = await self._request("session/list", params)
        return response

    async def delete_session(self, params: DeleteSessionRequest) -> DeleteSessionResponse:
        """Send ``session/delete``."""
        response: DeleteSessionResponse = await self._request("session/delete", params)
        return response

    async def archive_session(self, params: ArchiveSessionRequest) -> ArchiveSessionResponse:
        """Send ``session/archive``."""
        response: ArchiveSessionResponse = await self._request("session/archive", params)
        return response

    async def unarchive_session(self, params: UnarchiveSessionRequest) -> UnarchiveSessionResponse:
        """Send ``session/unarchive``."""
        response: UnarchiveSessionResponse = await self._request("session/unarchive", params)
        return response

    async def session_status(self, params: SessionStatusRequest) -> SessionStatusResponse:
        """Send ``session/status``."""
        response: SessionStatusResponse = await self._request("session/status", params)
        return response

    async def session_options(self, params: SessionOptionsRequest) -> SessionOptionsResponse:
        """Send ``session/options``."""
        response: SessionOptionsResponse = await self._request("session/options", params)
        return response

    async def set_session_title(self, params: SetSessionTitleRequest) -> SetSessionTitleResponse:
        """Send ``session/set_title``."""
        response: SetSessionTitleResponse = await self._request("session/set_title", params)
        return response

    async def set_session_config_option(
        self, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse:
        """Send ``session/set_config_option``."""
        response: SetSessionConfigOptionResponse = await self._request(
            "session/set_config_option", params
        )
        return response

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        """Send ``session/prompt``; returns once the message is inserted, not when the turn ends."""
        response: PromptResponse = await self._request("session/prompt", params)
        return response

    async def cancel(self, params: CancelSessionNotification) -> None:
        """Send ``session/cancel``."""
        await self._conn.send_notification("session/cancel", to_wire(params))

    async def cancel_tool_call(self, params: CancelToolCallRequest) -> CancelToolCallResponse:
        """Send ``tool_call/cancel``."""
        response: CancelToolCallResponse = await self._request("tool_call/cancel", params)
        return response

    async def cancel_terminal(self, params: CancelTerminalRequest) -> CancelTerminalResponse:
        """Send ``terminal/cancel``."""
        response: CancelTerminalResponse = await self._request("terminal/cancel", params)
        return response

    async def mcp_message(self, params: MessageMcpRequest) -> MessageMcpResponse:
        """Send an ``mcp/message`` request to the agent."""
        response: MessageMcpResponse = await self._request("mcp/message", params)
        return response

    async def mcp_notification(self, params: MessageMcpNotification) -> None:
        """Send an ``mcp/message`` notification to the agent."""
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
        """Wait until the agent disconnected or the connection was closed."""
        await self._conn.wait_closed()
