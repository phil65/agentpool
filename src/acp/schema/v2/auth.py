"""v2 authentication: auth methods, ``auth/login``, ``auth/logout`` and ``auth/status``.

An agent that returns a non-empty ``authMethods`` list in ``initialize`` MUST implement
``auth/login`` and ``auth/logout``; otherwise clients MUST NOT call them.
"""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Annotated, Literal

from pydantic import MISSING, Discriminator, Field, Tag

from acp.schema.v2.base import UNKNOWN_TAG, AnnotatedObject, Unknown, open_discriminator
from acp.schema.v2.common import AuthMethodId, EnvVariable  # noqa: TC001


class AuthMethodAgent(AnnotatedObject):
    """Agent handles authentication itself through ``auth/login``."""

    type: Literal["agent"] = Field(default="agent", init=False)

    method_id: AuthMethodId
    """Unique identifier for this authentication method."""

    name: str
    """Human-readable name of the authentication method."""

    description: str | MISSING | None = MISSING
    """Optional human-readable description of this authentication method."""


class AuthMethodTerminal(AnnotatedObject):
    """Terminal-based authentication method.

    The client runs the configured agent program as a separate interactive process, with
    ``args`` appended and ``env`` set, for the user to authenticate in a TUI. Requires the
    client's ``auth.terminal`` capability.
    """

    type: Literal["terminal"] = Field(default="terminal", init=False)

    method_id: AuthMethodId
    """Unique identifier for this authentication method."""

    name: str
    """Human-readable name of the authentication method."""

    description: str | MISSING | None = MISSING
    """Optional human-readable description of this authentication method."""

    args: Sequence[str] | MISSING = MISSING
    """Additional arguments to append to the configured agent invocation for terminal auth."""

    env: Sequence[EnvVariable] | MISSING = MISSING
    """Additional environment variables to set on the configured agent invocation for terminal
    auth. Names MUST be unique. These values override same-named variables in the base launch
    configuration."""


class UnknownAuthMethod(Unknown):
    """A custom or future auth method; ``type`` values starting with ``_`` are extensions."""

    type: str
    """The method type."""

    method_id: AuthMethodId
    """Unique identifier for this authentication method."""

    name: str
    """Human-readable name of the authentication method."""


AuthMethod = Annotated[
    Annotated[AuthMethodTerminal, Tag("terminal")]
    | Annotated[AuthMethodAgent, Tag("agent")]
    | Annotated[UnknownAuthMethod, Tag(UNKNOWN_TAG)],
    Discriminator(open_discriminator("type", frozenset({"terminal", "agent"}))),
]
"""An authentication method the agent offers, discriminated by ``type``."""


AuthState = Literal["authenticated", "unauthenticated", "not_required", "unknown"] | str
"""**UNSTABLE**: The agent's overall authentication state.

``authenticated`` does not guarantee valid credentials. Values starting with ``_`` are
extensions.
"""


class LoginAuthRequest(AnnotatedObject):
    """Request parameters for the ``auth/login`` method."""

    method_id: AuthMethodId
    """The ID of the authentication method to use. Must be one of the methods advertised in the
    initialize response."""


class LoginAuthResponse(AnnotatedObject):
    """Response to the ``auth/login`` method."""


class LogoutAuthRequest(AnnotatedObject):
    """Request parameters for the ``auth/logout`` method."""


class LogoutAuthResponse(AnnotatedObject):
    """Response to the ``auth/logout`` method."""


class AuthAccount(AnnotatedObject):
    """**UNSTABLE**: An account or credential the agent has configured.

    For example a signed-in user account or an API key. All fields are display
    information only.
    """

    label: str
    """Human-readable name for the account, suitable for display on its own, for example
    ``Claude Max``, ``OpenAI API key``, or ``Vertex AI``."""

    description: str | MISSING | None = MISSING
    """Short text shown with the label, such as the credential source, for example ``API key
    helper``. MUST NOT contain credentials."""

    active: bool | MISSING | None = MISSING
    """Whether the agent currently uses this account by default."""

    name: str | MISSING | None = MISSING
    """Display name of the user, such as a username or full name."""

    email: str | MISSING | None = MISSING
    """Email address of the user."""

    organization: str | MISSING | None = MISSING
    """Display name of the organization, team, or workspace the account is using."""


class AuthStatusRequest(AnnotatedObject):
    """**UNSTABLE**: Request parameters for the ``auth/status`` method."""


class AuthStatusResponse(AnnotatedObject):
    """**UNSTABLE**: Response to the ``auth/status`` method."""

    state: AuthState
    """Whether the agent can start sessions without the user signing in first."""

    accounts: Sequence[AuthAccount] | MISSING = MISSING
    """The accounts the agent has configured, for display."""


class AuthStatusUpdateNotification(AnnotatedObject):
    """**UNSTABLE**: Notification sent by the agent when its authentication status changes."""

    state: AuthState
    """Whether the agent can start sessions without the user signing in first."""

    accounts: Sequence[AuthAccount] | MISSING = MISSING
    """The accounts the agent has configured, for display."""
