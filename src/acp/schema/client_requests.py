"""Client request schema definitions."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Any, Literal, Self

from pydantic import Field

from acp.schema.base import AnnotatedObject, Request
from acp.schema.capabilities import (
    AuthCapabilities,
    ClientCapabilities,
    ClientSessionCapabilities,
    CompactionCapabilities,
    FileSystemCapabilities,
    NoticeCapabilities,
    PromptSuggestionsCapabilities,
)
from acp.schema.common import Implementation
from acp.schema.config_options import InitialSessionConfigOption  # noqa: TC001
from acp.schema.content_blocks import ContentBlock  # noqa: TC001
from acp.schema.mcp import McpServer, MessageMcpRequest
from acp.schema.providers import (
    DisableProviderRequest,
    ListProvidersRequest,
    SetProviderRequest,
)
from acp.schema.session_state import SessionArchiveFilter  # noqa: TC001


SystemPromptMode = Literal["append", "override"] | str
"""How the Agent combines a client-provided system prompt with its own instructions.

- ``append``: Add the content to the Agent's own instructions.
- ``override``: Replace the Agent's built-in default instructions
  (requires ``session_capabilities.system_prompt.mode.override``).
"""


class SystemPrompt(AnnotatedObject):
    """Client-provided system-level instructions for a session.

    Only allowed if the Agent advertises ``session_capabilities.system_prompt``.
    """

    mode: SystemPromptMode
    """How the Agent combines ``content`` with its own instructions."""

    content: Sequence[ContentBlock]
    """The system prompt content."""


class SessionSetupRequest(Request):
    """Fields shared by the session lifecycle requests (new, load, resume, fork)."""

    cwd: str
    """The working directory for this session. Must be an absolute path."""

    additional_directories: Sequence[str] = Field(default_factory=list)
    """Additional workspace roots for this session. Each path must be absolute.

    Requires the ``session_capabilities.additional_directories`` capability.
    """

    mcp_servers: Sequence[McpServer] = Field(default_factory=list)
    """List of MCP (Model Context Protocol) servers the agent should connect to."""

    system_prompt: SystemPrompt | None = None
    """System-level instructions for the session.

    Requires the ``session_capabilities.system_prompt`` capability.
    """

    config_options: Sequence[InitialSessionConfigOption] = Field(default_factory=list)
    """**UNSTABLE**: Config option values to apply when the session is set up."""


class CustomRequest(Request):
    """Request for custom/extension methods."""

    method: str
    """The custom method name (without underscore prefix)."""

    data: dict[str, Any]
    """The method parameters."""


class NewSessionRequest(SessionSetupRequest):
    """Request parameters for creating a new session.

    See protocol docs: [Creating a Session](https://agentclientprotocol.com/protocol/session-setup#creating-a-session)
    """


class LoadSessionRequest(SessionSetupRequest):
    """Request parameters for loading an existing session.

    Only available if the Agent supports the `loadSession` capability.

    See protocol docs: [Loading Sessions](https://agentclientprotocol.com/protocol/session-setup#loading-sessions)
    """

    session_id: str
    """The ID of the session to load."""


class ListSessionsRequest(Request):
    """Request parameters for listing existing sessions.

    Only available if the Agent supports the `session.list` capability.
    """

    cursor: str | None = None
    """Opaque cursor token from a previous response's nextCursor field for pagination."""

    cwd: str | None = None
    """Filter sessions by working directory. Must be an absolute path."""

    archived: SessionArchiveFilter | None = None
    """**UNSTABLE**: Which sessions to return by archive state. Defaults to ``exclude``."""


class ForkSessionRequest(SessionSetupRequest):
    """**UNSTABLE**: Request parameters for forking an existing session.

    Creates a new, active session based on the context of an existing one.

    Only available if the Agent supports the `session.fork` capability.
    """

    session_id: str
    """The ID of the session to fork."""

    cursor_id: str | None = None
    """**UNSTABLE**: Fork at this session cursor instead of the end of the session.

    The new session's history is the source history before the cursor. Only IDs of
    cursors announced on the source session that currently advertise
    ``capabilities.fork`` are valid; Agents reject others with ``-32602``.
    """


class ResumeSessionRequest(SessionSetupRequest):
    """Request parameters for resuming an existing session.

    Resumes an existing session without returning previous messages (unlike `session/load`).
    ``mcp_servers`` is the complete client-provided set.

    Only available if the Agent supports the `session.resume` capability.
    """

    session_id: str
    """The ID of the session to resume."""


class DeleteSessionRequest(Request):
    """Request parameters for deleting a session from ``session/list``.

    Only available if the Agent supports the ``session.delete`` capability.
    """

    session_id: str
    """The ID of the session to delete."""


class ArchiveSessionRequest(Request):
    """**UNSTABLE**: Request parameters for archiving a session.

    Only available if the Agent supports the ``session.archive`` capability.
    """

    session_id: str
    """The ID of the session to archive."""


class UnarchiveSessionRequest(Request):
    """**UNSTABLE**: Request parameters for unarchiving a session.

    Only available if the Agent supports the ``session.archive`` capability.
    """

    session_id: str
    """The ID of the session to unarchive."""


class SessionStatusRequest(Request):
    """**UNSTABLE**: Request parameters for checking whether the Agent has a session in memory.

    Only available if the Agent supports the ``session.status`` capability.
    """

    session_id: str
    """The ID of the session to check."""


class SessionOptionsRequest(Request):
    """**UNSTABLE**: Request the modes and config options a session would start with.

    Only available if the Agent supports the ``session.options`` capability.
    """

    cwd: str
    """The working directory to resolve options for. Must be an absolute path."""

    additional_directories: Sequence[str] = Field(default_factory=list)
    """Additional workspace roots to resolve options for. Each path must be absolute."""

    session_id: str | None = None
    """The session to resolve options for."""


class SetSessionTitleRequest(Request):
    """**UNSTABLE**: Request parameters for renaming a session.

    Only available if the Agent supports the ``session.title`` capability.
    """

    session_id: str
    """The ID of the session to retitle."""

    title: str
    """The new title for the session."""


class SetSessionModeRequest(Request):
    """Request parameters for setting a session mode."""

    mode_id: str
    """The ID of the mode to set."""

    session_id: str
    """The ID of the session to set the mode for."""


class PromptRequest(Request):
    """Request parameters for sending a user prompt to the agent.

    Contains the user's message and any additional context.

    See protocol docs: [User Message](https://agentclientprotocol.com/protocol/prompt-turn#1-user-message)
    """

    prompt: Sequence[ContentBlock]
    """The blocks of content that compose the user's message.

    As a baseline, the Agent MUST support [`ContentBlock::Text`] and
    [`ContentBlock::ResourceContentBlock`],
    while other variants are optionally enabled via [`PromptCapabilities`].

    The Client MUST adapt its interface according to [`PromptCapabilities`].

    The client MAY include referenced pieces of context as either
    [`ContentBlock::Resource`] or [`ContentBlock::ResourceContentBlock`].

    When available, [`ContentBlock::Resource`] is preferred
    as it avoids extra round-trips and allows the message to include
    pieces of context from sources the agent may not have access to.
    """

    session_id: str
    """The ID of the session to send this user message to."""


class SetSessionConfigOptionRequest(Request):
    """Request parameters for setting a session configuration option.

    Supports both select (string value ID) and boolean config options.
    When ``type`` is ``"boolean"``, ``value`` is a bool.
    When ``type`` is absent or ``"value_id"``, ``value`` is a string.

    See protocol docs: [Session Config Options](https://agentclientprotocol.com/protocol/session-config-options)
    """

    config_id: str
    """The ID of the configuration option to set."""

    session_id: str
    """The ID of the session to set the config option for."""

    type: str | None = None
    """The value type discriminator. ``"boolean"`` for bool values, absent for string value IDs."""

    value: str | bool
    """The value to set. String for select options, bool for boolean options."""


class InitializeRequest(Request):
    """Request parameters for the initialize method.

    Sent by the client to establish connection and negotiate capabilities.

    See protocol docs: [Initialization](https://agentclientprotocol.com/protocol/initialization)
    """

    client_capabilities: ClientCapabilities | None = Field(default_factory=ClientCapabilities)
    """Capabilities supported by the client."""

    client_info: Implementation | None = None
    """Information about the Client name and version sent to the Agent.

    Note: in future versions of the protocol, this will be required.
    """

    protocol_version: int
    """The latest protocol version supported by the client."""

    @classmethod
    def create(
        cls,
        title: str,
        name: str,
        version: str,
        terminal: bool = True,
        read_text_file: bool = True,
        write_text_file: bool = True,
        protocol_version: int = 1,
        terminal_auth: bool = False,
        prompt_suggestions: bool = False,
        compaction: bool = False,
        notices: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> Self:
        """Create a new InitializeRequest instance."""
        fs = FileSystemCapabilities(read_text_file=read_text_file, write_text_file=write_text_file)
        auth = AuthCapabilities(terminal=terminal_auth) if terminal_auth else None
        session = ClientSessionCapabilities(
            prompt_suggestions=PromptSuggestionsCapabilities() if prompt_suggestions else None,
            compaction=CompactionCapabilities() if compaction else None,
            notices=NoticeCapabilities() if notices else None,
        )
        caps = ClientCapabilities(terminal=terminal, fs=fs, auth=auth, session=session)
        impl = Implementation(title=title, name=name, version=version)
        return cls(
            client_capabilities=caps,
            client_info=impl,
            protocol_version=protocol_version,
            field_meta=metadata,
        )

    @classmethod
    def create_for_package(
        cls,
        package_name: str,
        allow_terminal: bool = True,
        allow_file_operations: bool = True,
        terminal_auth: bool = False,
        prompt_suggestions: bool = False,
        compaction: bool = False,
        notices: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> InitializeRequest:
        """Create ACP initialize request for app with given name (based on pkg metadata)."""
        from importlib.metadata import metadata as _metadata

        pkg_meta = _metadata(package_name)
        return cls.create(
            title=pkg_meta["Name"],
            version=pkg_meta["Version"],
            name=package_name,
            protocol_version=1,
            terminal=allow_terminal,
            read_text_file=allow_file_operations,
            write_text_file=allow_file_operations,
            terminal_auth=terminal_auth,
            prompt_suggestions=prompt_suggestions,
            compaction=compaction,
            notices=notices,
            metadata=metadata,
        )


class CloseSessionRequest(Request):
    """Request parameters for closing an active session.

    The agent **must** cancel any ongoing work related to the session
    (treat it as if ``session/cancel`` was called) and then free up any resources
    associated with the session.

    Only available if the Agent supports the ``session.close`` capability.
    """

    session_id: str
    """The ID of the session to close."""


class AuthenticateRequest(Request):
    """Request parameters for the authenticate method.

    Specifies which authentication method to use.
    """

    method_id: str
    """The ID of the authentication method to use.

    Must be one of the methods advertised in the initialize response.
    """


class LogoutRequest(Request):
    """Request parameters for the logout method.

    Terminates the current authenticated session.

    After a successful logout, all new sessions will require authentication.
    There is no guarantee about the behavior of already running sessions.
    """


class AuthStatusRequest(Request):
    """**UNSTABLE**: Request the agent's current authentication state.

    Only available if the Agent supports the ``auth.status`` capability.
    """


ClientRequest = (
    InitializeRequest
    | AuthenticateRequest
    | LogoutRequest
    | AuthStatusRequest
    | NewSessionRequest
    | LoadSessionRequest
    | ListSessionsRequest
    | DeleteSessionRequest
    | ArchiveSessionRequest
    | UnarchiveSessionRequest
    | SessionStatusRequest
    | SessionOptionsRequest
    | SetSessionTitleRequest
    | ForkSessionRequest
    | ResumeSessionRequest
    | CloseSessionRequest
    | SetSessionModeRequest
    | SetSessionConfigOptionRequest
    | PromptRequest
    | ListProvidersRequest
    | SetProviderRequest
    | DisableProviderRequest
    | MessageMcpRequest
    | CustomRequest
)
