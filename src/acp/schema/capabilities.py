"""Capability schema."""

from __future__ import annotations

from typing import Self

from pydantic import Field

from acp.schema.base import AnnotatedObject
from acp.schema.elicitation import ElicitationCapabilities  # noqa: TC001


class FileSystemCapabilities(AnnotatedObject):
    """File system capabilities that a client may support.

    See protocol docs: [FileSystem](https://agentclientprotocol.com/protocol/initialization#filesystem)
    """

    read_text_file: bool | None = False
    """Whether the Client supports `fs/read_text_file` requests."""

    write_text_file: bool | None = False
    """Whether the Client supports `fs/write_text_file` requests."""


class AuthCapabilities(AnnotatedObject):
    """Authentication capabilities supported by the client.

    Advertised during initialization to inform the agent which authentication
    method types the client can handle.
    """

    terminal: bool = False
    """Whether the client supports ``terminal`` authentication methods."""


class CompactionCapabilities(AnnotatedObject):
    """Client support for ID-addressed context compaction updates.

    ``{}`` advertises the complete compaction contract.
    """


class NoticeCapabilities(AnnotatedObject):
    """Client support for presenting live ``notice`` session updates to the user."""


class BooleanConfigOptionCapabilities(AnnotatedObject):
    """Capabilities for boolean session configuration options."""


class SessionConfigOptionsCapabilities(AnnotatedObject):
    """Session configuration option capabilities supported by the client."""

    boolean: BooleanConfigOptionCapabilities | None = None
    """Whether the client supports boolean session configuration options."""


class PromptSuggestionsImageCapabilities(AnnotatedObject):
    """**UNSTABLE**: Prompt suggestions may include ``image`` content blocks."""


class PromptSuggestionsAudioCapabilities(AnnotatedObject):
    """**UNSTABLE**: Prompt suggestions may include ``audio`` content blocks."""


class PromptSuggestionsEmbeddedContextCapabilities(AnnotatedObject):
    """**UNSTABLE**: Prompt suggestions may include ``resource`` content blocks."""


class PromptSuggestionsCapabilities(AnnotatedObject):
    """**UNSTABLE**: Client support for ``prompt_suggestions_update`` session updates.

    Text and resource link content are always allowed.
    """

    image: PromptSuggestionsImageCapabilities | None = None
    """Whether prompt suggestions may include ``image`` content blocks."""

    audio: PromptSuggestionsAudioCapabilities | None = None
    """Whether prompt suggestions may include ``audio`` content blocks."""

    embedded_context: PromptSuggestionsEmbeddedContextCapabilities | None = None
    """Whether prompt suggestions may include ``resource`` content blocks."""


class ClientSessionCapabilities(AnnotatedObject):
    """Session-related capabilities supported by the client."""

    compaction: CompactionCapabilities | None = None
    """Support for ID-addressed context compaction updates."""

    config_options: SessionConfigOptionsCapabilities | None = None
    """Config option capabilities supported by the client."""

    notices: NoticeCapabilities | None = None
    """Support for live user-facing ``notice`` session updates."""

    prompt_suggestions: PromptSuggestionsCapabilities | None = None
    """**UNSTABLE**: Support for ``prompt_suggestions_update`` session updates."""


class SubagentCapabilities(AnnotatedObject):
    """**UNSTABLE**: Client support for ``subagent_update`` session updates."""


class PlanCapabilities(AnnotatedObject):
    """**UNSTABLE**: Client support for ID-addressed ``plan_update`` / ``plan_removed`` updates."""


class ClientCapabilities(AnnotatedObject):
    """Capabilities supported by the client.

    Advertised during initialization to inform the agent about
    available features and methods.

    See protocol docs: [Client Capabilities](https://agentclientprotocol.com/protocol/initialization#client-capabilities)
    """

    auth: AuthCapabilities | None = Field(default_factory=AuthCapabilities)
    """Authentication capabilities supported by the client."""

    elicitation: ElicitationCapabilities | None = None
    """Elicitation capabilities supported by the client.

    Determines which elicitation modes the agent may use.
    """

    session: ClientSessionCapabilities | None = None
    """Session-related capabilities supported by the client."""

    subagents: SubagentCapabilities | None = None
    """**UNSTABLE**: Client support for ``subagent_update`` session updates."""

    plan: PlanCapabilities | None = None
    """**UNSTABLE**: Client support for ID-addressed plan updates."""

    fs: FileSystemCapabilities | None = Field(default_factory=FileSystemCapabilities)
    """File system capabilities supported by the client.

    Determines which file operations the agent can request.
    """

    terminal: bool | None = False
    """Whether the Client support all `terminal/*` methods."""

    @classmethod
    def create(
        cls,
        read_text_file: bool | None = False,
        write_text_file: bool | None = False,
        terminal: bool | None = False,
        auth: AuthCapabilities | None = None,
    ) -> Self:
        """Create a new instance of ClientCapabilities.

        Args:
            read_text_file: Whether the Client supports `fs/read_text_file` requests.
            write_text_file: Whether the Client supports `fs/write_text_file` requests.
            terminal: Whether the Client supports all `terminal/*` methods.
            auth: Authentication capabilities supported by the client.

        Returns:
            A new instance of ClientCapabilities.
        """
        fs = FileSystemCapabilities(read_text_file=read_text_file, write_text_file=write_text_file)
        return cls(fs=fs, terminal=terminal, auth=auth)


class PromptCapabilities(AnnotatedObject):
    """Prompt capabilities supported by the agent in `session/prompt` requests.

    Baseline agent functionality requires support for [`ContentBlock::Text`]
    and [`ContentBlock::ResourceContentBlock`] in prompt requests.

    Other variants must be explicitly opted in to.
    Capabilities for different types of content in prompt requests.

    Indicates which content types beyond the baseline (text and resource links)
    the agent can process.

    See protocol docs: [Prompt Capabilities](https://agentclientprotocol.com/protocol/initialization#prompt-capabilities)
    """

    audio: bool | None = False
    """Agent supports [`ContentBlock::Audio`]."""

    embedded_context: bool | None = False
    """Agent supports embedded context in `session/prompt` requests.

    When enabled, the Client is allowed to include [`ContentBlock::Resource`]
    in prompt requests for pieces of context that are referenced in the message.
    """

    image: bool | None = False
    """Agent supports [`ContentBlock::Image`]."""


class McpCapabilities(AnnotatedObject):
    """MCP capabilities supported by the agent."""

    http: bool | None = False
    """Agent supports [`McpServer::Http`]."""

    sse: bool | None = False
    """Agent supports [`McpServer::Sse`]."""

    acp: bool | None = False
    """**UNSTABLE**: Agent supports ``acp`` MCP servers (MCP over ACP via ``mcp/message``)."""


class SessionListCapabilities(AnnotatedObject):
    """Capabilities for the `session/list` method.

    By supplying `{}` it means that the agent supports listing of sessions.
    """


class SessionDeleteCapabilities(AnnotatedObject):
    """Capabilities for the ``session/delete`` method."""


class SessionAdditionalDirectoriesCapabilities(AnnotatedObject):
    """Support for ``additionalDirectories`` on session lifecycle requests."""


class SessionSystemPromptOverrideCapabilities(AnnotatedObject):
    """Support for the ``override`` system prompt mode."""


class SessionSystemPromptModeCapabilities(AnnotatedObject):
    """System prompt delivery modes the agent supports in addition to ``append``."""

    override: SessionSystemPromptOverrideCapabilities | None = None
    """Whether the agent supports ``override`` mode."""


class SessionSystemPromptCapabilities(AnnotatedObject):
    """Capabilities for client-provided system prompts."""

    mode: SessionSystemPromptModeCapabilities | None = None
    """Delivery modes the agent supports in addition to ``append``."""


class SessionArchiveCapabilities(AnnotatedObject):
    """**UNSTABLE**: Support for ``session/archive`` and ``session/unarchive``."""


class SessionStatusCapabilities(AnnotatedObject):
    """**UNSTABLE**: Support for ``session/status``."""


class SessionOptionsCapabilities(AnnotatedObject):
    """**UNSTABLE**: Support for ``session/options``."""


class SessionTitleCapabilities(AnnotatedObject):
    """**UNSTABLE**: Support for ``session/set_title``."""


class SessionCancelCapabilities(AnnotatedObject):
    """**UNSTABLE**: Permits the client to cancel a subagent's current work."""


class SubagentSessionCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities of a subagent session."""

    cancel: SessionCancelCapabilities | None = None
    """Permits the client to cancel this child's current work without ending the session."""


class SessionForkCapabilities(AnnotatedObject):
    """Capabilities for the `session/fork` method.

    **UNSTABLE**: This capability is not part of the spec yet,
    and may be removed or changed at any point.

    By supplying `{}` it means that the agent supports forking of sessions.
    """


class SessionResumeCapabilities(AnnotatedObject):
    """Capabilities for the `session/resume` method.

    By supplying `{}` it means that the agent supports resuming of sessions.
    """


class SessionCloseCapabilities(AnnotatedObject):
    """Capabilities for the `session/close` method.

    By supplying ``{}`` it means that the agent supports closing of sessions.
    """


class SessionCapabilities(AnnotatedObject):
    """Session capabilities supported by the agent.

    As a baseline, all Agents **MUST** support `session/new`, `session/prompt`,
    `session/cancel`, and `session/update`.

    Optionally, they **MAY** support other session methods and notifications
    by specifying additional capabilities.

    Note: `session/load` is still handled by the top-level `load_session` capability.
    This will be unified in future versions of the protocol.

    See protocol docs: [Session Capabilities](https://agentclientprotocol.com/protocol/initialization#session-capabilities)
    """

    list: SessionListCapabilities | None = None
    """Whether the agent supports `session/list`."""

    delete: SessionDeleteCapabilities | None = None
    """Whether the agent supports ``session/delete``."""

    additional_directories: SessionAdditionalDirectoriesCapabilities | None = None
    """Whether the agent supports ``additionalDirectories`` on session lifecycle requests."""

    system_prompt: SessionSystemPromptCapabilities | None = None
    """Whether the agent supports ``systemPrompt`` on session lifecycle requests."""

    resume: SessionResumeCapabilities | None = None
    """Whether the agent supports `session/resume`."""

    close: SessionCloseCapabilities | None = None
    """Whether the agent supports `session/close`."""

    fork: SessionForkCapabilities | None = None
    """**UNSTABLE**: Whether the agent supports `session/fork`."""

    archive: SessionArchiveCapabilities | None = None
    """**UNSTABLE**: Whether the agent supports ``session/archive`` and ``session/unarchive``."""

    status: SessionStatusCapabilities | None = None
    """**UNSTABLE**: Whether the agent supports ``session/status``."""

    options: SessionOptionsCapabilities | None = None
    """**UNSTABLE**: Whether the agent supports ``session/options``."""

    title: SessionTitleCapabilities | None = None
    """**UNSTABLE**: Whether the agent supports ``session/set_title``."""


class LogoutCapabilities(AnnotatedObject):
    """Logout capabilities supported by the agent.

    By supplying ``{}`` it means that the agent supports the logout method.
    """


class AuthStatusCapabilities(AnnotatedObject):
    """**UNSTABLE**: Support for ``auth/status`` and ``auth/status_update``."""


class AgentAuthCapabilities(AnnotatedObject):
    """Authentication-related capabilities supported by the agent."""

    logout: LogoutCapabilities | None = None
    """Whether the agent supports the logout method."""

    status: AuthStatusCapabilities | None = None
    """**UNSTABLE**: Whether the agent supports ``auth/status``."""


class ProvidersCapabilities(AnnotatedObject):
    """**UNSTABLE**: Support for ``providers/list``, ``providers/set`` and ``providers/disable``."""


class AgentCapabilities(AnnotatedObject):
    """Capabilities supported by the agent.

    Advertised during initialization to inform the client about
    available features and content types.

    See protocol docs: [Agent Capabilities](https://agentclientprotocol.com/protocol/initialization#agent-capabilities)
    """

    auth: AgentAuthCapabilities | None = Field(default_factory=AgentAuthCapabilities)
    """Authentication-related capabilities supported by the agent."""

    providers: ProvidersCapabilities | None = None
    """**UNSTABLE**: LLM provider configuration support."""

    load_session: bool | None = False
    """Whether the agent supports `session/load`."""

    mcp_capabilities: McpCapabilities | None = Field(default_factory=McpCapabilities)
    """MCP capabilities supported by the agent."""

    prompt_capabilities: PromptCapabilities | None = Field(default_factory=PromptCapabilities)
    """Prompt capabilities supported by the agent."""

    session_capabilities: SessionCapabilities | None = Field(default_factory=SessionCapabilities)
    """Session capabilities supported by the agent."""

    @classmethod
    def create(
        cls,
        load_session: bool | None = False,
        http_mcp_servers: bool = False,
        sse_mcp_servers: bool = False,
        audio_prompts: bool = False,
        embedded_context_prompts: bool = False,
        image_prompts: bool = False,
        list_sessions: bool = False,
        resume_session: bool = False,
        close_session: bool = False,
    ) -> Self:
        """Create an instance of AgentCapabilities.

        Args:
            load_session: Whether the agent supports `session/load`.
            http_mcp_servers: Whether the agent supports HTTP MCP servers.
            sse_mcp_servers: Whether the agent supports SSE MCP servers.
            audio_prompts: Whether the agent supports audio prompts.
            embedded_context_prompts: Whether the agent supports embedded context prompts.
            image_prompts: Whether the agent supports image prompts.
            list_sessions: Whether the agent supports `session/list`.
            resume_session: Whether the agent supports `session/resume`.
            close_session: Whether the agent supports `session/close`.
        """
        session_caps = SessionCapabilities(
            list=SessionListCapabilities() if list_sessions else None,
            resume=SessionResumeCapabilities() if resume_session else None,
            close=SessionCloseCapabilities() if close_session else None,
        )
        return cls(
            load_session=load_session,
            mcp_capabilities=McpCapabilities(http=http_mcp_servers, sse=sse_mcp_servers),
            prompt_capabilities=PromptCapabilities(
                audio=audio_prompts,
                embedded_context=embedded_context_prompts,
                image=image_prompts,
            ),
            session_capabilities=session_caps,
        )
