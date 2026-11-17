"""Message schema definitions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from acp.schema.agent_requests import AgentRequest  # noqa: TC001
from acp.schema.agent_responses import AgentResponse  # noqa: TC001
from acp.schema.base import Schema
from acp.schema.client_requests import ClientRequest  # noqa: TC001
from acp.schema.client_responses import ClientResponse  # noqa: TC001
from acp.schema.common import Error  # noqa: TC001
from acp.schema.notifications import (  # noqa: TC001
    CancelNotification,
    SessionNotification,
)


AgentMethod = Literal[
    "auth/status",
    "authenticate",
    "initialize",
    "logout",
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
    "session/load",
    "session/new",
    "session/options",
    "session/prompt",
    "session/resume",
    "session/set_config_option",
    "session/set_mode",
    "session/set_title",
    "session/status",
    "session/unarchive",
]
"""Methods handled by the agent."""

ClientMethod = Literal[
    "auth/status_update",
    "elicitation/complete",
    "elicitation/create",
    "fs/read_text_file",
    "fs/write_text_file",
    "mcp/message",
    "session/request_permission",
    "session/update",
    "terminal/create",
    "terminal/kill",
    "terminal/output",
    "terminal/release",
    "terminal/wait_for_exit",
]
"""Methods handled by the client."""

ProtocolMethod = Literal["$/cancel_request"]
"""Methods either side may send."""

ProxyMethod = Literal["proxy/initialize", "proxy/successor"]
"""**UNSTABLE**: Methods handled by proxies in a proxy chain."""


class JsonRPCMessage(Schema):
    """JSON-RPC 2.0 message."""

    jsonrpc: Literal["2.0"] = Field(default="2.0", init=False)
    """JSON RPC Messsage."""


class ClientNotificationMessage(JsonRPCMessage):
    """A message (request, response, or notification) with `"jsonrpc": "2.0"`.

    Specified as [required by JSON-RPC 2.0 Specification][1].

    [1]: https://www.jsonrpc.org/specification#compatibility
    """

    method: ClientMethod | str
    """Method name."""

    params: CancelNotification | Any | None = None
    """Agent notification parameters."""


class ClientResponseMessage(JsonRPCMessage):
    """A message (request, response, or notification) with `"jsonrpc": "2.0"`.

    Specified as [required by JSON-RPC 2.0 Specification][1].

    [1]: https://www.jsonrpc.org/specification#compatibility
    """

    id: int | str | None = None
    """JSON RPC Request Id."""

    result: ClientResponse | Any
    """All possible responses that a client can send to an agent.

    This enum is used internally for routing RPC responses. You typically won't need
    to use this directly - the responses are handled automatically by the connection.
    These are responses to the corresponding `AgentRequest` variants."""


class AgentResponseMessage(JsonRPCMessage):
    """A message (request, response, or notification) with `"jsonrpc": "2.0"`.

    Specified as [required by JSON-RPC 2.0 Specification][1].

    [1]: https://www.jsonrpc.org/specification#compatibility
    """

    id: int | str | None = None
    """JSON RPC Request Id."""

    result: AgentResponse | Any
    """All possible responses that an agent can send to a client.

    This enum is used internally for routing RPC responses. You typically won't need
    to use this directly - the responses are handled automatically by the connection.
    These are responses to the corresponding `ClientRequest` variants."""


class ClientRequestMessage(JsonRPCMessage):
    """A message (request, response, or notification) with `"jsonrpc": "2.0"`.

    Specified as [required by JSON-RPC 2.0 Specification][1].

    [1]: https://www.jsonrpc.org/specification#compatibility
    """

    id: int | str | None = None
    """JSON RPC Request Id."""

    method: ClientMethod | str
    """Method name."""

    params: ClientRequest | Any | None = None
    """Client request parameters."""


class AgentRequestMessage(JsonRPCMessage):
    """A message (request, response, or notification) with `"jsonrpc": "2.0"`.

    Specified as [required by JSON-RPC 2.0 Specification][1].

    [1]: https://www.jsonrpc.org/specification#compatibility
    """

    id: int | str | None = None
    """JSON RPC Request Id."""

    method: AgentMethod | str
    """Method name."""

    params: AgentRequest | Any | None = None
    """Agent request parameters."""


class AgentNotificationMessage(JsonRPCMessage):
    """A message (request, response, or notification) with `"jsonrpc": "2.0"`.

    Specified as [required by JSON-RPC 2.0 Specification][1].

    [1]: https://www.jsonrpc.org/specification#compatibility
    """

    method: AgentMethod | str
    """Method name."""

    params: SessionNotification | Any | None = None
    """Agent notification parameters."""


class ErrorMessage(JsonRPCMessage):
    """A message (request, response, or notification) with `"jsonrpc": "2.0"`.

    Specified as
    [required by JSON-RPC 2.0 Specification][1].

    [1]: https://www.jsonrpc.org/specification#compatibility
    """

    id: int | str | None = None
    """JSON RPC Request Id."""

    error: Error
