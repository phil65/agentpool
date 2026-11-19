"""v2 subagent sessions: child sessions created and owned by a parent session.

Part of the v2 baseline: clients handle ``subagent_update``, child sessions and
session-directed messages without a capability.
"""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Literal

from pydantic import MISSING, Field

from acp.schema.v2.base import AnnotatedObject
from acp.schema.v2.common import AbsolutePath, SessionId  # noqa: TC001
from acp.schema.v2.prompt import StateUpdate  # noqa: TC001


class SessionCancelCapabilities(AnnotatedObject):
    """A subagent session's work can be cancelled (``session/cancel``) without ending it."""


class SubagentSessionCapabilities(AnnotatedObject):
    """Client-initiated mutations permitted for a specific subagent session.

    Each mutation needs an explicit per-child capability.
    """

    cancel: SessionCancelCapabilities | MISSING | None = MISSING
    """Permits cancelling the child's current work."""


class SubagentUpdate(AnnotatedObject):
    """The parent session created and owns a child session, or the association changed.

    Fields other than ``session_id`` are patches: omitted leaves them unchanged,
    ``None`` clears them.
    """

    session_update: Literal["subagent_update"] = Field(default="subagent_update", init=False)

    session_id: SessionId
    """The opaque ID of the child session in all ACP messages."""

    title: str | MISSING | None = MISSING
    """The parent's display title for the child; need not be unique."""

    description: str | MISSING | None = MISSING
    """The parent's description of the child's role or purpose."""

    capabilities: SubagentSessionCapabilities | MISSING | None = MISSING
    """Client-initiated mutations permitted for the child."""

    state: StateUpdate | MISSING | None = MISSING
    """The child's current foreground state, mirrored onto the association."""

    cwd: AbsolutePath | MISSING | None = MISSING
    """The child's working directory."""

    additional_directories: Sequence[AbsolutePath] | MISSING | None = MISSING
    """Additional workspace roots of the child."""
