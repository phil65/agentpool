"""Authentication status of an agent backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


AuthState = Literal["authenticated", "unauthenticated", "not_required", "unknown"]


@dataclass(frozen=True, kw_only=True)
class AuthAccount:
    """An account the agent backend has configured, for display. Never contains secrets."""

    label: str
    """Human-readable name, e.g. ``Claude Max`` or ``OpenAI API key``."""
    description: str | None = None
    """Short text shown with the label, e.g. the credential source."""
    active: bool | None = None
    """Whether the backend currently uses this account by default."""
    name: str | None = None
    """Display name of the user."""
    email: str | None = None
    """Email address of the user."""
    organization: str | None = None
    """Organization, team, or workspace the account is using."""


@dataclass(frozen=True, kw_only=True)
class AuthStatus:
    """Whether the agent can start sessions without the user signing in first."""

    state: AuthState
    accounts: list[AuthAccount] = field(default_factory=list)
