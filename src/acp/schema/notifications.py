"""Notification schema definitions."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Any

from pydantic import Field

from acp.schema.base import AnnotatedObject
from acp.schema.common import AuthAccount, AuthState  # noqa: TC001
from acp.schema.elicitation import ElicitationCompleteNotification
from acp.schema.mcp import MessageMcpNotification
from acp.schema.session_updates import SessionUpdate  # noqa: TC001


class SessionNotification(AnnotatedObject):
    """Notification containing a session update from the agent.

    Used to stream real-time progress and results during prompt processing.

    See protocol docs: [Agent Reports Output](https://agentclientprotocol.com/protocol/prompt-turn#3-agent-reports-output)
    """

    session_id: str
    """The ID of the session this update pertains to."""

    update: SessionUpdate
    """The session update data."""


class CancelNotification(AnnotatedObject):
    """Notification to cancel ongoing operations for a session.

    This is a notification sent by the client to cancel an ongoing prompt turn.

    Upon receiving this notification, the Agent SHOULD:
    - Stop all language model requests as soon as possible
    - Abort all tool call invocations in progress
    - Send any pending `session/update` notifications
    - Respond to the original `session/prompt` request with `StopReason::Cancelled`

    See protocol docs: [Cancellation](https://agentclientprotocol.com/protocol/prompt-turn#cancellation)
    """

    session_id: str
    """The ID of the session to cancel operations for."""


class CancelRequestNotification(AnnotatedObject):
    """Notification to cancel an ongoing request (``$/cancel_request``).

    Either side may send it for a request it issued. The receiver should stop
    the work and may reply with a ``-32800`` (request cancelled) error or a normal
    response. Unknown or already answered request IDs are ignored.
    """

    request_id: int | str | None
    """The ID of the request to cancel."""


class AuthStatusUpdateNotification(AnnotatedObject):
    """**UNSTABLE**: The agent's authentication state changed (``auth/status_update``)."""

    state: AuthState
    """Whether the agent can start sessions without the user signing in first."""

    accounts: Sequence[AuthAccount] = Field(default_factory=list)
    """The accounts the agent has configured, for display."""


class ExtNotification(AnnotatedObject):
    """Extension notification from client or agent.

    Allows sending arbitrary notifications that are not part of the ACP spec.
    Extension notifications provide a way to send one-way messages for custom
    functionality while maintaining protocol compatibility.

    The method name should be prefixed with an underscore (e.g., `_myExtension/notify`).

    See protocol docs: [Extensibility](https://agentclientprotocol.com/protocol/extensibility)
    """

    method: str
    """The extension method name (should be prefixed with underscore)."""

    params: dict[str, Any] | None = None
    """Optional parameters for the notification."""


AgentNotification = (
    SessionNotification
    | ElicitationCompleteNotification
    | AuthStatusUpdateNotification
    | MessageMcpNotification
    | CancelRequestNotification
    | ExtNotification
)
"""All possible notifications that an agent can send to a client.

This is used internally for routing RPC notifications.
Notifications do not expect a response.
"""

ClientNotification = (
    CancelNotification | MessageMcpNotification | CancelRequestNotification | ExtNotification
)
"""All possible notifications that a client can send to an agent.

This is used internally for routing RPC notifications.
Notifications do not expect a response.
"""
