"""v2 capabilities exchanged in ``initialize``.

Support markers are objects: ``{}`` means supported, omitted or ``null`` means not
supported. Capabilities of individual objects (tool calls, terminals, cursors,
subagent sessions) live with those objects.
"""

from __future__ import annotations

from pydantic import MISSING

from acp.schema.v2.base import AnnotatedObject


class TerminalAuthCapabilities(AnnotatedObject):
    """Capabilities for terminal authentication methods."""


class ClientAuthCapabilities(AnnotatedObject):
    """Authentication capabilities supported by the client."""

    terminal: TerminalAuthCapabilities | MISSING | None = MISSING
    """Whether the client supports ``terminal`` authentication methods."""


class AuthStatusCapabilities(AnnotatedObject):
    """**UNSTABLE**: Auth status capabilities supported by the agent."""


class AgentAuthCapabilities(AnnotatedObject):
    """Authentication-related extension capabilities supported by the agent."""

    status: AuthStatusCapabilities | MISSING | None = MISSING
    """Whether the agent supports the ``auth/status`` method."""


class ElicitationFormCapabilities(AnnotatedObject):
    """Form-based elicitation capabilities."""


class ElicitationUrlCapabilities(AnnotatedObject):
    """URL-based elicitation capabilities."""


class ElicitationCapabilities(AnnotatedObject):
    """Elicitation capabilities supported by the client."""

    form: ElicitationFormCapabilities | MISSING | None = MISSING
    """Whether the client supports form-based elicitation."""

    url: ElicitationUrlCapabilities | MISSING | None = MISSING
    """Whether the client supports URL-based elicitation."""


class CommandsSkillCapabilities(AnnotatedObject):
    """**UNSTABLE**: Client support for ``skill`` command entries."""


class CommandsCapabilities(AnnotatedObject):
    """**UNSTABLE**: Command types the client understands in addition to ``command``."""

    skill: CommandsSkillCapabilities | MISSING | None = MISSING
    """Support for ``skill`` command entries."""


class PromptSuggestionsImageCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities for image content in prompt suggestions."""


class PromptSuggestionsAudioCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities for audio content in prompt suggestions."""


class PromptSuggestionsEmbeddedContextCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities for embedded resources in prompt suggestions."""


class PromptSuggestionsCapabilities(AnnotatedObject):
    """**UNSTABLE**: Client support for ``prompt_suggestions_update`` session updates."""

    image: PromptSuggestionsImageCapabilities | MISSING | None = MISSING
    """Whether prompt suggestions may include ``image`` content blocks."""

    audio: PromptSuggestionsAudioCapabilities | MISSING | None = MISSING
    """Whether prompt suggestions may include ``audio`` content blocks."""

    embedded_context: PromptSuggestionsEmbeddedContextCapabilities | MISSING | None = MISSING
    """Whether prompt suggestions may include ``resource`` content blocks."""


class ClientSessionCapabilities(AnnotatedObject):
    """**UNSTABLE**: Session-related capabilities supported by the client."""

    prompt_suggestions: PromptSuggestionsCapabilities | MISSING | None = MISSING
    """Support for ``prompt_suggestions_update`` session updates."""

    commands: CommandsCapabilities | MISSING | None = MISSING
    """Command types the client understands in addition to ``command``."""


class ClientCapabilities(AnnotatedObject):
    """Capabilities supported by the client."""

    auth: ClientAuthCapabilities | MISSING | None = MISSING
    """Authentication capabilities supported by the client. Determines which authentication
    method types the agent may include in its ``InitializeResponse``."""

    elicitation: ElicitationCapabilities | MISSING | None = MISSING
    """Elicitation capabilities supported by the client. Determines which elicitation modes the
    agent may use."""

    session: ClientSessionCapabilities | MISSING | None = MISSING
    """Session-related capabilities supported by the client."""


class PromptImageCapabilities(AnnotatedObject):
    """Capabilities for image content in prompt requests."""


class PromptAudioCapabilities(AnnotatedObject):
    """Capabilities for audio content in prompt requests."""


class PromptEmbeddedContextCapabilities(AnnotatedObject):
    """Capabilities for embedded context in prompt requests."""


class PromptSkillInlineCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities for ``skill`` content blocks at any position in a prompt."""


class PromptSkillCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities for ``skill`` content blocks in prompt requests."""

    inline: PromptSkillInlineCapabilities | MISSING | None = MISSING
    """Agent supports ``skill`` content blocks at any position in a prompt."""


class PromptCapabilities(AnnotatedObject):
    """Prompt capabilities supported by the agent in ``session/prompt`` requests."""

    image: PromptImageCapabilities | MISSING | None = MISSING
    """Agent supports ``image`` content blocks."""

    audio: PromptAudioCapabilities | MISSING | None = MISSING
    """Agent supports ``audio`` content blocks."""

    embedded_context: PromptEmbeddedContextCapabilities | MISSING | None = MISSING
    """Agent supports embedded context in ``session/prompt`` requests."""

    skill: PromptSkillCapabilities | MISSING | None = MISSING
    """Agent supports ``skill`` content blocks."""


class McpStdioCapabilities(AnnotatedObject):
    """Capabilities for stdio MCP server transports."""


class McpHttpCapabilities(AnnotatedObject):
    """Capabilities for HTTP MCP server transports."""


class McpAcpCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities for ACP MCP server transports."""


class McpCapabilities(AnnotatedObject):
    """MCP capabilities supported by the agent for session lifecycle requests."""

    stdio: McpStdioCapabilities | MISSING | None = MISSING
    """Agent supports ``stdio`` MCP servers."""

    http: McpHttpCapabilities | MISSING | None = MISSING
    """Agent supports ``http`` MCP servers."""

    acp: McpAcpCapabilities | MISSING | None = MISSING
    """Agent supports ``acp`` MCP servers."""


class SessionDeleteCapabilities(AnnotatedObject):
    """Capabilities for the ``session/delete`` method."""


class SessionAdditionalDirectoriesCapabilities(AnnotatedObject):
    """Capabilities for additional session directories support."""


class SessionSystemPromptOverrideCapabilities(AnnotatedObject):
    """Capabilities for the ``override`` system prompt mode."""


class SessionSystemPromptModeCapabilities(AnnotatedObject):
    """System prompt delivery modes the agent supports in addition to ``append``."""

    override: SessionSystemPromptOverrideCapabilities | MISSING | None = MISSING
    """Whether the agent supports ``override`` mode."""


class SessionSystemPromptCapabilities(AnnotatedObject):
    """Capabilities for client-provided system prompts."""

    mode: SessionSystemPromptModeCapabilities | MISSING | None = MISSING
    """Delivery modes the agent supports in addition to ``append``."""


class SessionForkCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities for the ``session/fork`` method."""


class SessionArchiveCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities for ``session/archive`` and ``session/unarchive``."""


class SessionStatusCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities for the ``session/status`` method."""


class SessionOptionsCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities for the ``session/options`` method."""


class SessionTitleCapabilities(AnnotatedObject):
    """**UNSTABLE**: Capabilities for Client-set session titles."""


class SessionCapabilities(AnnotatedObject):
    """Session capabilities supported by the agent.

    ``{}`` means the agent supports the baseline session methods: ``session/new``,
    ``session/list``, ``session/resume``, ``session/close``, ``session/prompt``,
    ``session/cancel`` and ``session/update``.
    """

    prompt: PromptCapabilities | MISSING | None = MISSING
    """Prompt capabilities supported by the agent in ``session/prompt`` requests."""

    mcp: McpCapabilities | MISSING | None = MISSING
    """MCP capabilities supported by the agent for session lifecycle requests."""

    delete: SessionDeleteCapabilities | MISSING | None = MISSING
    """Whether the agent supports ``session/delete``."""

    additional_directories: SessionAdditionalDirectoriesCapabilities | MISSING | None = MISSING
    """Whether the agent supports ``additionalDirectories`` on supported session lifecycle
    requests."""

    system_prompt: SessionSystemPromptCapabilities | MISSING | None = MISSING
    """Whether the agent supports ``systemPrompt`` on session lifecycle requests."""

    fork: SessionForkCapabilities | MISSING | None = MISSING
    """Whether the agent supports ``session/fork``."""

    archive: SessionArchiveCapabilities | MISSING | None = MISSING
    """Whether the agent supports ``session/archive`` and ``session/unarchive``."""

    status: SessionStatusCapabilities | MISSING | None = MISSING
    """Whether the agent supports ``session/status``."""

    options: SessionOptionsCapabilities | MISSING | None = MISSING
    """Whether the agent supports ``session/options``."""

    title: SessionTitleCapabilities | MISSING | None = MISSING
    """Whether the agent supports ``session/set_title``."""


class ProvidersCapabilities(AnnotatedObject):
    """**UNSTABLE**: Provider configuration capabilities supported by the agent."""


class AgentCapabilities(AnnotatedObject):
    """Capabilities supported by the agent."""

    session: SessionCapabilities | MISSING | None = MISSING
    """Session capabilities supported by the agent."""

    auth: AgentAuthCapabilities | MISSING | None = MISSING
    """Authentication-related extension capabilities supported by the agent."""

    providers: ProvidersCapabilities | MISSING | None = MISSING
    """Provider configuration capabilities supported by the agent."""
