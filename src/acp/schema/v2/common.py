"""Shared v2 types: identifiers, errors, implementation info, usage and annotations."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Any, Literal

from pydantic import MISSING, Field

from acp.schema.v2.base import AnnotatedObject, Schema


# --- Identifiers ------------------------------------------------------------------

SessionId = str
"""Unique identifier of a session."""

MessageId = str
"""Unique identifier of a message within a session."""

ToolCallId = str
"""Unique identifier of a tool call within a session."""

TerminalId = str
"""Unique identifier of a display terminal within a session."""

PlanId = str
"""Unique identifier of a plan within a session."""

CompactionId = str
"""Unique identifier of a context compaction within a session."""

CursorId = str
"""**UNSTABLE**: Agent-owned identifier of a session cursor, unique within its session."""

PermissionOptionId = str
"""Unique identifier of a permission option."""

ElicitationId = str
"""Unique identifier of an elicitation."""

AuthMethodId = str
"""Unique identifier of an authentication method."""

ProviderId = str
"""**UNSTABLE**: Unique identifier of an LLM provider."""

SessionConfigId = str
"""Unique identifier of a session config option."""

SessionConfigValueId = str
"""Unique identifier of a value of a select config option."""

SessionConfigGroupId = str
"""Unique identifier of a group of select config option values."""

SessionListCursor = str
"""Opaque pagination cursor of ``session/list``."""

McpServerAcpId = str
"""**UNSTABLE**: Identifier of an MCP server provided over ACP."""

McpRequestId = str
"""**UNSTABLE**: Identifier of an MCP request carried over ACP."""

IdempotencyKey = str
"""Client-chosen key that makes a ``session/prompt`` retry insert at most one message."""

MediaType = str
"""A MIME type, e.g. ``image/png``."""

AbsolutePath = str
"""An absolute filesystem path."""

ProtocolVersion = int
"""An ACP protocol version number."""

RequestId = int | str
"""A JSON-RPC request ID. Unlike base JSON-RPC, it MUST NOT be ``null``."""


# --- Errors -----------------------------------------------------------------------

ErrorCode = int
"""A JSON-RPC error code: one of :data:`ERROR_CODES` or another integer."""

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
REQUEST_CANCELLED = -32800
AUTH_REQUIRED = -32000
RESOURCE_NOT_FOUND = -32002
UNSUPPORTED_PROTOCOL_VERSION = -32022

ERROR_CODES: dict[int, str] = {
    PARSE_ERROR: "Parse error",
    INVALID_REQUEST: "Invalid request",
    METHOD_NOT_FOUND: "Method not found",
    INVALID_PARAMS: "Invalid params",
    INTERNAL_ERROR: "Internal error",
    REQUEST_CANCELLED: "Request cancelled",
    AUTH_REQUIRED: "Authentication required",
    RESOURCE_NOT_FOUND: "Resource not found",
    UNSUPPORTED_PROTOCOL_VERSION: "Unsupported protocol version",
}
"""Error codes the protocol defines, with their standard messages."""


class UnsupportedProtocolVersionErrorData(AnnotatedObject):
    """Data of the ``-32022`` (Unsupported protocol version) error."""

    requested: ProtocolVersion
    """The protocol version the Client requested."""

    supported: Sequence[ProtocolVersion]
    """The protocol versions the Agent supports."""


class SessionNotFoundErrorData(AnnotatedObject):
    """Data of the ``-32002`` (Resource not found) error for an unknown or inactive session."""

    session_id: SessionId
    """The session ID from the request."""


ErrorData = UnsupportedProtocolVersionErrorData | SessionNotFoundErrorData | Any
"""Data of an :class:`Error`: a shape ACP defines, or any other value."""


class Error(Schema):
    """A JSON-RPC error object."""

    code: ErrorCode
    """The error type, e.g. :data:`INVALID_PARAMS`."""

    message: str
    """A short, single-sentence description of the error."""

    data: ErrorData | MISSING | None = MISSING
    """Additional information about the error."""


# --- Implementation info ----------------------------------------------------------

IconTheme = Literal["light", "dark"] | str
"""Theme an icon is designed for. Values starting with ``_`` are extensions."""


class Icon(AnnotatedObject):
    """An optionally sized icon a user interface can display."""

    src: str
    """URI of the icon. MUST be an ``https:`` or ``data:`` URI."""

    mime_type: MediaType | MISSING | None = MISSING
    """MIME type override, if the source's type is missing or generic."""

    sizes: Sequence[str] | MISSING | None = MISSING
    """Sizes the icon can be used at, each ``WxH`` (e.g. ``48x48``) or ``any``."""

    theme: IconTheme | MISSING | None = MISSING
    """Theme the icon is designed for; omitted means not theme-specific."""


class Implementation(AnnotatedObject):
    """Name and version of a client or agent implementation."""

    name: str
    """Programmatic name; also the display name if ``title`` is not set."""

    title: str | MISSING | None = MISSING
    """Human-readable name for user interfaces."""

    version: str
    """Version of the implementation, e.g. ``1.0.0``."""


# --- Usage ------------------------------------------------------------------------


class Usage(AnnotatedObject):
    """**UNSTABLE**: Token usage of the foreground work that just completed (not cumulative)."""

    total_tokens: int = Field(ge=0)
    """Sum of all token types."""

    input_tokens: int = Field(ge=0)
    """Input tokens."""

    output_tokens: int = Field(ge=0)
    """Output tokens."""

    thought_tokens: int | MISSING | None = Field(default=MISSING, ge=0)
    """Thought/reasoning tokens, if reported."""

    cached_read_tokens: int | MISSING | None = Field(default=MISSING, ge=0)
    """Cache read tokens, if reported."""

    cached_write_tokens: int | MISSING | None = Field(default=MISSING, ge=0)
    """Cache write tokens, if reported."""


class Cost(AnnotatedObject):
    """Cumulative cost of a session."""

    amount: float
    """Total cumulative cost."""

    currency: str
    """Unit of ``amount``: an ISO 4217 code such as ``USD``, or another unit such as ``credits``."""


# --- Small shared shapes ----------------------------------------------------------


class EnvVariable(AnnotatedObject):
    """An environment variable to set when launching a process."""

    name: str
    """Name of the variable."""

    value: str
    """Value of the variable."""


class HttpHeader(AnnotatedObject):
    """An HTTP header to send with requests."""

    name: str
    """Name of the header."""

    value: str
    """Value of the header."""


Role = Literal["assistant", "user"] | str
"""Sender or recipient in a conversation. Values starting with ``_`` are extensions."""


class Annotations(AnnotatedObject):
    """Hints for the client on how an object is used or displayed."""

    audience: Sequence[Role] | MISSING | None = MISSING
    """Intended recipients, such as the user or the assistant."""

    last_modified: str | MISSING | None = MISSING
    """When the underlying resource was last modified (ISO 8601)."""

    priority: float | MISSING | None = Field(default=MISSING, ge=0, le=1)
    """Importance from 0 (least) to 1 (most important)."""
