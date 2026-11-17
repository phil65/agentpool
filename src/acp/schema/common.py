"""Common schema definitions."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Annotated, Any, Literal

from pydantic import Discriminator, Field, Tag

from acp.schema.base import AnnotatedObject, Schema


class EnvVariable(AnnotatedObject):
    """An environment variable to set when launching an MCP server."""

    name: str
    """The name of the environment variable."""

    value: str
    """The value to set for the environment variable."""


class Implementation(AnnotatedObject):
    """Describes the name and version of an ACP implementation.

    Includes an optional title for UI representation.
    """

    name: str
    """Intended for programmatic or logical use.

    Can be used as a display name fallback if title isn't present."""

    title: str | None = None
    """Intended for UI and end-user contexts.

    Optimized to be human-readable and easily understood.
    If not provided, the name should be used for display."""

    version: str
    """Version of the implementation.

    Can be displayed to the user or used for debugging or metrics purposes."""


class AuthMethodAgent(AnnotatedObject):
    """Agent handles authentication itself.

    This is the default authentication method type.
    When no ``type`` field is present, the method is treated as ``agent``.
    """

    type: Literal["agent"] = Field(default="agent", exclude=True)
    """Discriminator field.  Defaults to ``"agent"`` and is excluded from serialization
    for backward compatibility."""

    id: str
    """Unique identifier for this authentication method."""

    name: str
    """Human-readable name of the authentication method."""

    description: str | None = None
    """Optional description providing more details about this authentication method."""


class AuthMethodTerminal(AnnotatedObject):
    """Terminal authentication method.

    Client runs the configured agent program as a separate interactive process
    for the user to authenticate.
    """

    type: Literal["terminal"] = "terminal"
    """Discriminator field."""

    id: str
    """Unique identifier for this authentication method."""

    name: str
    """Human-readable name of the authentication method."""

    description: str | None = None
    """Optional description providing more details about this authentication method."""

    args: Sequence[str] = ()
    """Additional arguments to pass when running the agent binary for terminal auth."""

    env: dict[str, str] = Field(default_factory=dict)
    """Additional environment variables to set when running the agent binary for terminal auth."""


def _auth_method_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("type", "agent")  # type: ignore[no-any-return]
    return getattr(v, "type", "agent")


AuthMethod = Annotated[
    Annotated[AuthMethodAgent, Tag("agent")] | Annotated[AuthMethodTerminal, Tag("terminal")],
    Discriminator(_auth_method_discriminator),
]
"""Describes an available authentication method.

The ``type`` field acts as the discriminator.  When no ``type`` is present,
the method is treated as ``agent``.
"""


class Error(Schema):
    """JSON-RPC error object.

    Represents an error that occurred during method execution, following the
    JSON-RPC 2.0 error object specification with optional additional data.

    See protocol docs: [JSON-RPC Error Object](https://www.jsonrpc.org/specification#error_object)
    """

    code: ErrorCode
    """A number indicating the error type that occurred.

    This must be an integer as defined in the JSON-RPC specification.
    """

    data: Any | None = None
    """Optional primitive or structured value that contains additional errorinformation.

    This may include debugging information or context-specific details.
    """

    message: str
    """A string providing a short description of the error.

    The message should be limited to a concise single sentence.
    """


PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
REQUEST_CANCELLED = -32800
AUTH_REQUIRED = -32000
RESOURCE_NOT_FOUND = -32002

ErrorCode = int
"""JSON-RPC error code. See the ``*_ERROR`` / ``*_REQUIRED`` constants for predefined codes."""


AuthMethodId = str
"""Unique identifier for an authentication method."""

AuthState = Literal["authenticated", "unauthenticated", "not_required", "unknown"] | str
"""**UNSTABLE**: Whether the agent can start sessions without the user signing in first."""


class AuthAccount(AnnotatedObject):
    """**UNSTABLE**: An account the agent has configured, for display."""

    label: str
    """Human-readable name for the account, e.g. ``Claude Max`` or ``OpenAI API key``."""

    description: str | None = None
    """Short text shown with the label, e.g. the credential source. MUST NOT contain secrets."""

    active: bool | None = None
    """Whether the agent currently uses this account by default."""

    name: str | None = None
    """Display name of the user."""

    email: str | None = None
    """Email address of the user."""

    organization: str | None = None
    """Display name of the organization, team, or workspace the account is using."""
