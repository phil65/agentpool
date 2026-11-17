from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import Field

from acp.schema.base import Response
from acp.schema.capabilities import AgentCapabilities
from acp.schema.common import AuthAccount, AuthMethod, AuthState, Implementation
from acp.schema.config_options import SessionConfigOption  # noqa: TC001
from acp.schema.mcp import MessageMcpResponse
from acp.schema.providers import (
    DisableProviderResponse,
    ListProvidersResponse,
    SetProviderResponse,
)
from acp.schema.session_state import SessionInfo, SessionModeState, SessionStatus
from acp.schema.session_updates import Usage  # noqa: TC001


if TYPE_CHECKING:
    from acp.schema import SessionMode


StopReason = Literal[
    "end_turn",
    "max_tokens",
    "max_turn_requests",
    "refusal",
    "cancelled",
]


class CustomResponse(Response):
    """Response for custom/extension methods."""

    data: dict[str, Any] | None = None
    """The method result data."""


def _mode_state(
    modes: Sequence[SessionMode] | None, current: str | None
) -> SessionModeState | None:
    if modes and current:
        return SessionModeState(available_modes=modes, current_mode_id=current)
    return None


class NewSessionResponse(Response):
    """Response from creating a new session.

    See protocol docs: [Creating a Session](https://agentclientprotocol.com/protocol/session-setup#creating-a-session)
    """

    modes: SessionModeState | None = None
    """Initial mode state if supported by the Agent

    See protocol docs: [Session Modes](https://agentclientprotocol.com/protocol/session-modes)
    """

    config_options: Sequence[SessionConfigOption] | None = None
    """**Configuration options for this session."""

    session_id: str
    """Unique identifier for the created session.

    Used in all subsequent requests for this conversation.
    """


class LoadSessionResponse(Response):
    """Response from loading an existing session."""

    modes: SessionModeState | None = None
    """Initial mode state if supported by the Agent

    See protocol docs: [Session Modes](https://agentclientprotocol.com/protocol/session-modes)
    """

    config_options: Sequence[SessionConfigOption] = []
    """The full list of config options with updated values."""


class ForkSessionResponse(Response):
    """**UNSTABLE**: Response from forking an existing session."""

    modes: SessionModeState | None = None
    """Initial mode state if supported by the Agent

    See protocol docs: [Session Modes](https://agentclientprotocol.com/protocol/session-modes)
    """

    session_id: str
    """Unique identifier for the newly created forked session."""

    config_options: Sequence[SessionConfigOption] = []
    """The full list of config options with updated values."""

    @classmethod
    def create(
        cls,
        session_id: str,
        config_options: Sequence[SessionConfigOption] | None = None,
        modes: Sequence[SessionMode] | None = None,
        current_mode: str | None = None,
    ) -> Self:
        """Create a response with the given session ID and config options."""
        return cls(
            session_id=session_id,
            config_options=config_options or [],
            modes=_mode_state(modes, current_mode),
        )


class ResumeSessionResponse(Response):
    """Response from resuming an existing session."""

    modes: SessionModeState | None = None
    """Initial mode state if supported by the Agent

    See protocol docs: [Session Modes](https://agentclientprotocol.com/protocol/session-modes)
    """

    config_options: Sequence[SessionConfigOption] = []
    """The full list of config options with updated values."""

    @classmethod
    def create(
        cls,
        config_options: Sequence[SessionConfigOption] | None = None,
        modes: Sequence[SessionMode] | None = None,
        current_mode: str | None = None,
    ) -> Self:
        """Create a response with the given config options and modes."""
        return cls(config_options=config_options or [], modes=_mode_state(modes, current_mode))


class SetSessionModeResponse(Response):
    """Response to `session/set_mode` method."""


class SetSessionConfigOptionResponse(Response):
    """Response to `session/set_config_option` method."""

    config_options: Sequence[SessionConfigOption] = []
    """The full list of config options with updated values."""


class PromptResponse(Response):
    """Response from processing a user prompt.

    See protocol docs: [Check for Completion](https://agentclientprotocol.com/protocol/prompt-turn#4-check-for-completion)
    """

    stop_reason: StopReason
    """Indicates why the agent stopped processing the turn."""

    usage: Usage | None = None
    """**UNSTABLE**: Token usage for this turn (optional)."""


class CloseSessionResponse(Response):
    """Response from closing a session."""


class DeleteSessionResponse(Response):
    """Response to ``session/delete``."""


class ArchiveSessionResponse(Response):
    """**UNSTABLE**: Response to ``session/archive``."""


class UnarchiveSessionResponse(Response):
    """**UNSTABLE**: Response to ``session/unarchive``."""


class SessionStatusResponse(Response):
    """**UNSTABLE**: Response to ``session/status``."""

    status: SessionStatus
    """Whether the Agent has the session in memory."""


class SessionOptionsResponse(Response):
    """**UNSTABLE**: The modes and config options a session would start with."""

    modes: SessionModeState | None = None
    """The mode state the session would start with, if the Agent supports modes."""

    config_options: Sequence[SessionConfigOption] | None = None
    """The config options the session would start with."""


class SetSessionTitleResponse(Response):
    """**UNSTABLE**: Response to ``session/set_title``."""

    title: str
    """The title the Agent stored."""


class AuthenticateResponse(Response):
    """Response to authenticate method."""


class LogoutResponse(Response):
    """Response to the ``logout`` method."""


class AuthStatusResponse(Response):
    """**UNSTABLE**: Response to ``auth/status``."""

    state: AuthState
    """Whether the agent can start sessions without the user signing in first."""

    accounts: Sequence[AuthAccount] = Field(default_factory=list)
    """The accounts the agent has configured, for display."""


class InitializeResponse(Response):
    """Response from the initialize method.

    Contains the negotiated protocol version and agent capabilities.

    See protocol docs: [Initialization](https://agentclientprotocol.com/protocol/initialization)
    """

    agent_capabilities: AgentCapabilities | None = Field(default_factory=AgentCapabilities)
    """Capabilities supported by the agent."""

    agent_info: Implementation | None = None
    """Information about the Agent name and version sent to the Client.


    Note: in future versions of the protocol, this will be required."""

    auth_methods: Sequence[AuthMethod] | None = Field(default_factory=list)
    """Authentication methods supported by the agent."""

    protocol_version: int = Field(ge=0, le=65535)
    """The protocol version the client specified if supported by the agent.

    Or the latest protocol version supported by the agent.
    The client should disconnect, if it doesn't support this version.
    """

    @classmethod
    def create(
        cls,
        name: str,
        title: str,
        version: str,
        protocol_version: int,
        load_session: bool | None = False,
        http_mcp_servers: bool = False,
        sse_mcp_servers: bool = False,
        audio_prompts: bool = False,
        embedded_context_prompts: bool = False,
        image_prompts: bool = False,
        list_sessions: bool = False,
        resume_session: bool = False,
        close_session: bool = False,
        auth_methods: Sequence[AuthMethod] | None = None,
    ) -> Self:
        """Create an instance of AgentCapabilities.

        Args:
            name: The name of the agent.
            title: The title of the agent.
            version: The version of the agent.
            protocol_version: The protocol version of the agent.
            load_session: Whether the agent supports `session/load`.
            http_mcp_servers: Whether the agent supports HTTP MCP servers.
            sse_mcp_servers: Whether the agent supports SSE MCP servers.
            audio_prompts: Whether the agent supports audio prompts.
            embedded_context_prompts: Whether the agent supports embedded context prompts.
            image_prompts: Whether the agent supports image prompts.
            list_sessions: Whether the agent supports `session/list`.
            resume_session: Whether the agent supports `session/resume`.
            close_session: Whether the agent supports `session/close`.
            auth_methods: The authentication methods supported by the agent.
        """
        caps = AgentCapabilities.create(
            load_session=load_session,
            http_mcp_servers=http_mcp_servers,
            sse_mcp_servers=sse_mcp_servers,
            audio_prompts=audio_prompts,
            embedded_context_prompts=embedded_context_prompts,
            image_prompts=image_prompts,
            list_sessions=list_sessions,
            resume_session=resume_session,
            close_session=close_session,
        )
        return cls(
            agent_info=Implementation(name=name, title=title, version=version),
            protocol_version=protocol_version,
            agent_capabilities=caps,
            auth_methods=auth_methods,
        )


class ListSessionsResponse(Response):
    """Response from listing sessions."""

    next_cursor: str | None = None
    """Opaque cursor token. If present, pass this in the next request's cursor parameter
    to fetch the next page. If absent, there are no more results."""

    sessions: Sequence[SessionInfo] = Field(default_factory=list)
    """Array of session information objects."""


AgentResponse = (
    InitializeResponse
    | AuthenticateResponse
    | LogoutResponse
    | AuthStatusResponse
    | NewSessionResponse
    | LoadSessionResponse
    | ForkSessionResponse
    | ResumeSessionResponse
    | CloseSessionResponse
    | ListSessionsResponse
    | DeleteSessionResponse
    | ArchiveSessionResponse
    | UnarchiveSessionResponse
    | SessionStatusResponse
    | SessionOptionsResponse
    | SetSessionTitleResponse
    | SetSessionModeResponse
    | SetSessionConfigOptionResponse
    | PromptResponse
    | ListProvidersResponse
    | SetProviderResponse
    | DisableProviderResponse
    | MessageMcpResponse
    | CustomResponse
)
