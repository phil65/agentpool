"""v2 JSON-RPC layer: methods, message envelopes, cancellation and proxies.

Envelopes keep ``params`` / ``result`` untyped: the method selects their type, and many
parameter types share fields (e.g. ``sessionId``), so they cannot be told apart by
shape. Parse them by method with the ``*_REQUESTS`` / ``*_NOTIFICATIONS`` tables, e.g.
``AGENT_REQUESTS[request.method].params.validate_python(request.params)``.

v2 does not use JSON-RPC batches: each transport message is a single request,
notification or response, and receivers reject an array with ``-32600``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import MISSING, Field, TypeAdapter

from acp.schema.v2.auth import (
    AuthStatusRequest,
    AuthStatusResponse,
    AuthStatusUpdateNotification,
    LoginAuthRequest,
    LoginAuthResponse,
    LogoutAuthRequest,
    LogoutAuthResponse,
)
from acp.schema.v2.base import AnnotatedObject, Schema
from acp.schema.v2.common import Error, RequestId  # noqa: TC001
from acp.schema.v2.config_options import (
    SetSessionConfigOptionRequest,
    SetSessionConfigOptionResponse,
)
from acp.schema.v2.elicitation import (
    CompleteElicitationNotification,
    CreateElicitationRequest,
    CreateElicitationResponse,
)
from acp.schema.v2.initialize import InitializeRequest, InitializeResponse
from acp.schema.v2.mcp import MessageMcpNotification, MessageMcpRequest, MessageMcpResponse
from acp.schema.v2.permissions import RequestPermissionRequest, RequestPermissionResponse
from acp.schema.v2.prompt import PromptRequest, PromptResponse
from acp.schema.v2.providers import (
    DisableProviderRequest,
    DisableProviderResponse,
    ListProvidersRequest,
    ListProvidersResponse,
    SetProviderRequest,
    SetProviderResponse,
)
from acp.schema.v2.session_updates import UpdateSessionNotification
from acp.schema.v2.sessions import (
    ArchiveSessionRequest,
    ArchiveSessionResponse,
    CancelSessionNotification,
    CloseSessionRequest,
    CloseSessionResponse,
    DeleteSessionRequest,
    DeleteSessionResponse,
    ForkSessionRequest,
    ForkSessionResponse,
    ListSessionsRequest,
    ListSessionsResponse,
    NewSessionRequest,
    NewSessionResponse,
    ResumeSessionRequest,
    ResumeSessionResponse,
    SessionOptionsRequest,
    SessionOptionsResponse,
    SessionStatusRequest,
    SessionStatusResponse,
    SetSessionTitleRequest,
    SetSessionTitleResponse,
    UnarchiveSessionRequest,
    UnarchiveSessionResponse,
)
from acp.schema.v2.terminals import CancelTerminalRequest, CancelTerminalResponse
from acp.schema.v2.tool_calls import CancelToolCallRequest, CancelToolCallResponse


# --- Methods ------------------------------------------------------------------------

AgentMethod = Literal[
    "auth/login",
    "auth/logout",
    "auth/status",
    "initialize",
    "mcp/message",
    "providers/disable",
    "providers/list",
    "providers/set",
    "session/archive",
    "session/cancel",
    "session/close",
    "session/delete",
    "session/fork",
    "session/list",
    "session/new",
    "session/options",
    "session/prompt",
    "session/resume",
    "session/set_config_option",
    "session/set_title",
    "session/status",
    "session/unarchive",
    "terminal/cancel",
    "tool_call/cancel",
]
"""Methods handled by the agent (``session/cancel`` is a notification)."""

ClientMethod = Literal[
    "auth/status_update",
    "elicitation/complete",
    "elicitation/create",
    "mcp/message",
    "session/request_permission",
    "session/update",
]
"""Methods handled by the client."""

ProtocolMethod = Literal["$/cancel_request"]
"""Methods either side may send."""

ProxyMethod = Literal["proxy/initialize", "proxy/successor"]
"""**UNSTABLE**: Methods handled by proxies in a proxy chain."""


# --- Envelopes --------------------------------------------------------------------


class _Message(Schema):
    jsonrpc: Literal["2.0"] = Field(default="2.0", init=False)
    """JSON-RPC version; always ``2.0``."""


class _Request(_Message):
    id: RequestId
    """Correlates the response; never ``null`` in ACP."""

    method: str
    """The method to invoke."""

    params: Any | MISSING = MISSING
    """Method-specific parameters; parse them with the method tables."""


class _Notification(_Message):
    method: str
    """The notification method."""

    params: Any | MISSING = MISSING
    """Method-specific parameters; parse them with the method tables."""


class ClientRequest(_Request):
    """A request the client sends to the agent (an :data:`AgentMethod`)."""


class AgentRequest(_Request):
    """A request the agent sends to the client (a :data:`ClientMethod`)."""


class ProxyRequest(_Request):
    """**UNSTABLE**: A request to a proxy in a proxy chain (a :data:`ProxyMethod`)."""


class ClientNotification(_Notification):
    """A notification the client sends to the agent."""


class AgentNotification(_Notification):
    """A notification the agent sends to the client."""


class ProxyNotification(_Notification):
    """**UNSTABLE**: A notification to a proxy in a proxy chain."""


class ResultResponse(_Message):
    """A successful JSON-RPC response."""

    id: RequestId
    """The ID of the request answered."""

    result: Any
    """The method-specific result; parse it with the method tables."""


class ErrorResponse(_Message):
    """A failed JSON-RPC response."""

    id: RequestId | None
    """The ID of the request answered; ``None`` if it could not be determined."""

    error: Error
    """The error."""


AgentResponse = ResultResponse | ErrorResponse
"""A response the agent sends to a client request."""

ClientResponse = ResultResponse | ErrorResponse
"""A response the client sends to an agent request."""

ProxyResponse = ResultResponse | ErrorResponse
"""**UNSTABLE**: A response of a proxy."""


# --- Cancellation and proxies -------------------------------------------------------


class CancelRequestNotification(AnnotatedObject):
    """Parameters of ``$/cancel_request``: cancel an ongoing request.

    The request must be one the sender of the notification issued.
    """

    request_id: RequestId
    """The request to cancel."""


class InitializeProxyRequest(InitializeRequest):
    """**UNSTABLE**: Parameters of ``proxy/initialize``, sent instead of ``initialize``.

    A conductor sends it to every component that has a successor.
    """


class InitializeProxyResponse(InitializeResponse):
    """**UNSTABLE**: Response to ``proxy/initialize``; the same as an ``initialize`` response."""


class ProxySuccessorRequest(AnnotatedObject):
    """**UNSTABLE**: Parameters of a ``proxy/successor`` request: a wrapped request."""

    method: str
    """Method of the wrapped request."""

    params: Any | MISSING = MISSING
    """Params of the wrapped request, forwarded unchanged."""


class ProxySuccessorNotification(AnnotatedObject):
    """**UNSTABLE**: Parameters of a ``proxy/successor`` notification: a wrapped notification."""

    method: str
    """Method of the wrapped notification."""

    params: Any | MISSING = MISSING
    """Params of the wrapped notification, forwarded unchanged."""


ProxySuccessorResponse = Any
"""**UNSTABLE**: Result of ``proxy/successor``: the wrapped request's result, unchanged."""


# --- Method tables ------------------------------------------------------------------


@dataclass(frozen=True)
class RequestTypes:
    """Validators of a request method's parameters and result."""

    params: TypeAdapter[Any]
    """Parses and validates the ``params`` of the request."""

    result: TypeAdapter[Any]
    """Parses and validates the ``result`` of the response."""

    @classmethod
    def of(cls, params: Any, result: Any) -> RequestTypes:
        """Build the validators from parameter and result types."""
        return cls(TypeAdapter(params), TypeAdapter(result))


AGENT_REQUESTS: dict[str, RequestTypes] = {
    "initialize": RequestTypes.of(InitializeRequest, InitializeResponse),
    "auth/login": RequestTypes.of(LoginAuthRequest, LoginAuthResponse),
    "auth/logout": RequestTypes.of(LogoutAuthRequest, LogoutAuthResponse),
    "auth/status": RequestTypes.of(AuthStatusRequest, AuthStatusResponse),
    "providers/list": RequestTypes.of(ListProvidersRequest, ListProvidersResponse),
    "providers/set": RequestTypes.of(SetProviderRequest, SetProviderResponse),
    "providers/disable": RequestTypes.of(DisableProviderRequest, DisableProviderResponse),
    "session/new": RequestTypes.of(NewSessionRequest, NewSessionResponse),
    "session/resume": RequestTypes.of(ResumeSessionRequest, ResumeSessionResponse),
    "session/fork": RequestTypes.of(ForkSessionRequest, ForkSessionResponse),
    "session/close": RequestTypes.of(CloseSessionRequest, CloseSessionResponse),
    "session/list": RequestTypes.of(ListSessionsRequest, ListSessionsResponse),
    "session/delete": RequestTypes.of(DeleteSessionRequest, DeleteSessionResponse),
    "session/archive": RequestTypes.of(ArchiveSessionRequest, ArchiveSessionResponse),
    "session/unarchive": RequestTypes.of(UnarchiveSessionRequest, UnarchiveSessionResponse),
    "session/status": RequestTypes.of(SessionStatusRequest, SessionStatusResponse),
    "session/options": RequestTypes.of(SessionOptionsRequest, SessionOptionsResponse),
    "session/set_title": RequestTypes.of(SetSessionTitleRequest, SetSessionTitleResponse),
    "session/set_config_option": RequestTypes.of(
        SetSessionConfigOptionRequest, SetSessionConfigOptionResponse
    ),
    "session/prompt": RequestTypes.of(PromptRequest, PromptResponse),
    "tool_call/cancel": RequestTypes.of(CancelToolCallRequest, CancelToolCallResponse),
    "terminal/cancel": RequestTypes.of(CancelTerminalRequest, CancelTerminalResponse),
    "mcp/message": RequestTypes.of(MessageMcpRequest, MessageMcpResponse),
}
"""Requests the agent handles."""

AGENT_NOTIFICATIONS: dict[str, TypeAdapter[Any]] = {
    "session/cancel": TypeAdapter(CancelSessionNotification),
    "mcp/message": TypeAdapter(MessageMcpNotification),
}
"""Notifications the agent handles."""

CLIENT_REQUESTS: dict[str, RequestTypes] = {
    "session/request_permission": RequestTypes.of(
        RequestPermissionRequest, RequestPermissionResponse
    ),
    "elicitation/create": RequestTypes.of(CreateElicitationRequest, CreateElicitationResponse),
    "mcp/message": RequestTypes.of(MessageMcpRequest, MessageMcpResponse),
}
"""Requests the client handles."""

CLIENT_NOTIFICATIONS: dict[str, TypeAdapter[Any]] = {
    "session/update": TypeAdapter(UpdateSessionNotification),
    "elicitation/complete": TypeAdapter(CompleteElicitationNotification),
    "auth/status_update": TypeAdapter(AuthStatusUpdateNotification),
    "mcp/message": TypeAdapter(MessageMcpNotification),
}
"""Notifications the client handles."""

PROTOCOL_NOTIFICATIONS: dict[str, TypeAdapter[Any]] = {
    "$/cancel_request": TypeAdapter(CancelRequestNotification),
}
"""Notifications either side handles."""

PROXY_REQUESTS: dict[str, RequestTypes] = {
    "proxy/initialize": RequestTypes.of(InitializeProxyRequest, InitializeProxyResponse),
    "proxy/successor": RequestTypes.of(ProxySuccessorRequest, ProxySuccessorResponse),
}
"""**UNSTABLE**: Requests a proxy handles."""

PROXY_NOTIFICATIONS: dict[str, TypeAdapter[Any]] = {
    "proxy/successor": TypeAdapter(ProxySuccessorNotification),
}
"""**UNSTABLE**: Notifications a proxy handles."""
