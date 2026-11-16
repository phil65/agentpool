from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import Field

from acp.schema.base import Response
from acp.schema.capabilities import AgentCapabilities
from acp.schema.common import AuthMethod, Implementation
from acp.schema.config_options import SessionConfigOption  # noqa: TC001
from acp.schema.session_state import SessionInfo, SessionModelState, SessionModeState  # noqa: TC001
from acp.schema.session_updates import Usage  # noqa: TC001


if TYPE_CHECKING:
    from acp.schema import ModelInfo, SessionMode


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


class SetSessionModelResponse(Response):
    """**UNSTABLE**: This capability is not part of the spec yet.

    Response to `session/set_model` method.
    """


class NewSessionResponse(Response):
    """Response from creating a new session.

    See protocol docs: [Creating a Session](https://agentclientprotocol.com/protocol/session-setup#creating-a-session)
    """

    models: SessionModelState | None = None
    """**UNSTABLE**

    This capability is not part of the spec yet.

    Initial model state if supported by the Agent
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

    models: SessionModelState | None = None
    """**UNSTABLE**

    This capability is not part of the spec yet.

    Initial model state if supported by the Agent
    """

    modes: SessionModeState | None = None
    """Initial mode state if supported by the Agent

    See protocol docs: [Session Modes](https://agentclientprotocol.com/protocol/session-modes)
    """

    config_options: Sequence[SessionConfigOption] = []
    """The full list of config options with updated values."""


class ForkSessionResponse(Response):
    """**UNSTABLE**: This capability is not part of the spec yet.

    Response from forking an existing session.
    """

    models: SessionModelState | None = None
    """**UNSTABLE**

    This capability is not part of the spec yet.

    Initial model state if supported by the Agent
    """

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
        models: Sequence[ModelInfo] | None = None,
        current_model: str | None = None,
        modes: Sequence[SessionMode] | None = None,
        current_mode: str | None = None,
    ) -> Self:
        """Create a response with the given session ID and config options."""
        from acp.schema import SessionModelState, SessionModeState

        model_state = (
            SessionModelState(available_models=models, current_model_id=current_model)
            if models and current_model
            else None
        )
        mode_state = (
            SessionModeState(available_modes=modes, current_mode_id=current_mode)
            if modes and current_mode
            else None
        )
        return cls(
            session_id=session_id,
            config_options=config_options or [],
            models=model_state,
            modes=mode_state,
        )


class ResumeSessionResponse(Response):
    """**UNSTABLE**: This capability is not part of the spec yet.

    Response from resuming an existing session.
    """

    models: SessionModelState | None = None
    """**UNSTABLE**

    This capability is not part of the spec yet.

    Initial model state if supported by the Agent
    """

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
        models: Sequence[ModelInfo] | None = None,
        current_model: str | None = None,
        modes: Sequence[SessionMode] | None = None,
        current_mode: str | None = None,
    ) -> Self:
        """Create a response with the given session ID and config options."""
        from acp.schema import SessionModelState, SessionModeState

        model_state = (
            SessionModelState(available_models=models, current_model_id=current_model)
            if models and current_model
            else None
        )
        mode_state = (
            SessionModeState(available_modes=modes, current_mode_id=current_mode)
            if modes and current_mode
            else None
        )
        return cls(
            config_options=config_options or [],
            models=model_state,
            modes=mode_state,
        )


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

    user_message_id: str | None = None
    """**UNSTABLE**: The acknowledged user message ID.

    If the client provided a ``message_id`` in the ``PromptRequest``, the agent echoes
    it here to confirm it was recorded. If the client did not provide one, the agent
    MAY assign one and return it here. Absence of this field indicates the agent did
    not record a message ID.
    """


class CloseSessionResponse(Response):
    """**UNSTABLE**: This capability is not part of the spec yet.

    Response from stopping a session.
    """


class AuthenticateResponse(Response):
    """Response to authenticate method."""


class LogoutResponse(Response):
    """**UNSTABLE**: Response to the ``logout`` method."""


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
            list_sessions: Whether the agent supports `session/list` (unstable).
            resume_session: Whether the agent supports `session/resume` (unstable).
            close_session: Whether the agent supports `session/close` (unstable).
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
    """**UNSTABLE**: This capability is not part of the spec yet.

    Response from listing sessions.
    """

    next_cursor: str | None = None
    """Opaque cursor token. If present, pass this in the next request's cursor parameter
    to fetch the next page. If absent, there are no more results."""

    sessions: Sequence[SessionInfo] = Field(default_factory=list)
    """Array of session information objects."""


AgentResponse = (
    InitializeResponse
    | AuthenticateResponse
    | LogoutResponse
    | NewSessionResponse
    | LoadSessionResponse
    | ForkSessionResponse
    | ResumeSessionResponse
    | CloseSessionResponse
    | ListSessionsResponse
    | SetSessionModeResponse
    | SetSessionConfigOptionResponse
    | PromptResponse
    | SetSessionModelResponse
    | CustomResponse
)
