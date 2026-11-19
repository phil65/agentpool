"""v2 ``initialize``: protocol version negotiation, implementation info and capabilities."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003

from pydantic import MISSING

from acp.schema.v2.auth import AuthMethod  # noqa: TC001
from acp.schema.v2.base import AnnotatedObject
from acp.schema.v2.capabilities import AgentCapabilities, ClientCapabilities  # noqa: TC001
from acp.schema.v2.common import Implementation, ProtocolVersion  # noqa: TC001


class InitializeRequest(AnnotatedObject):
    """Request parameters for the initialize method."""

    protocol_version: ProtocolVersion
    """The latest protocol version supported by the client."""

    info: Implementation
    """Information about the implementation sending this initialize request."""

    capabilities: ClientCapabilities | MISSING = MISSING
    """Capabilities supported by the client."""


class InitializeResponse(AnnotatedObject):
    """Response to the ``initialize`` method."""

    protocol_version: ProtocolVersion
    """The highest protocol version supported by the agent that is at or below the version
    requested by the client. If no such version exists, initialization fails with error code
    -32022."""

    info: Implementation
    """Information about the implementation sending this initialize response."""

    capabilities: AgentCapabilities | MISSING = MISSING
    """Capabilities supported by the agent."""

    auth_methods: Sequence[AuthMethod] | MISSING = MISSING
    """Authentication methods supported by the agent."""
