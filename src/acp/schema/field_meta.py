"""Undocumented ``_meta`` field conventions used by ACP implementations.

The ACP protocol includes a generic ``_meta`` (``field_meta``) extension point on
most schema objects (via :class:`~acp.schema.base.AnnotatedObject`). Several
implementations — notably ``claude-agent-acp``, ``codex-acp``, and Zed — have
established conventions for what goes into these fields. None of these are part
of the official ACP specification.

This module provides typed dictionaries documenting every known convention so
that implementors can produce and consume them with type safety rather than
relying on raw ``dict[str, Any]`` access. Keys that are not valid Python
identifiers (``terminal-auth``, ``_claude/model``, …) use the functional
``TypedDict`` syntax so the types match the wire format exactly.

Sources (file paths only; line numbers change too often to be useful):
    - ``@agentclientprotocol/claude-agent-acp`` v0.108.1, ACP v1 code path:
      ``src/new-session-meta.ts``, ``src/tool-calls/renderer.ts``,
      ``src/tool-calls/client-capabilities.ts``, ``src/v1/acp-agent.ts``,
      ``src/v1/steering.ts``, ``src/session-usage.ts``, ``src/prompt-suggestions.ts``,
      ``src/client-system-prompt.ts``, ``src/providers.ts``
    - ``@agentclientprotocol/codex-acp`` v2.7.0 (same terminal, quota, gateway and
      steering conventions)
    - Zed ``main`` at ``3c2118dd6`` (2026-11-18):
      ``crates/agent_servers/src/acp/v1.rs`` (client capabilities, display-only
      terminals, terminal auth), ``crates/acp_thread/src/acp_thread.rs``
      (``tool_name``, ``subagent_session_info``, ``command_category``)

JetBrains AIR extensions (``_meta.jetbrains.air``) are documented in
claude-agent-acp's ``docs/air-extensions.md`` and are not modelled here.
"""

# No `from __future__ import annotations`: string annotations hide `NotRequired`
# from TypedDict at runtime, so `__required_keys__` would list optional keys.
from typing import Any, Literal, NotRequired, TypedDict


# ---------------------------------------------------------------------------
# Tool call / tool_call_update meta  (_meta on ToolCallStart / ToolCallProgress)
# ---------------------------------------------------------------------------


class McpServerInfo(TypedDict):
    """The MCP server an ``mcp__*`` tool belongs to."""

    name: str
    """Server name."""

    source: str
    """Where the server is configured."""


class ClaudeCodeToolMeta(TypedDict, total=False):
    """Claude Code-specific metadata attached to tool call updates.

    Produced by claude-agent-acp (``ToolUpdateMeta.claudeCode`` in
    ``src/tool-calls/renderer.ts``).
    """

    toolName: str
    """The Claude Code tool name (e.g. ``Bash``, ``Edit``, ``Read``).

    Also carried as the standard ACP ``name`` field on the initial ``tool_call``.
    """

    toolResponse: Any
    """Structured output from the tool execution.

    JetBrains AIR clients only get its ``status`` / ``isAsync`` markers; the
    tool output itself is in the tool call content.
    """

    parentToolUseId: str
    """For a tool call made inside a subagent: the tool use id of the
    ``Agent`` / ``Task`` call that spawned the subagent."""

    nonExecutionKind: str
    """On a ``failed`` ``tool_call_update``: why the tool never ran.

    An open set, e.g. ``user-rejected``, ``permission-rule``, ``interrupted``,
    ``cancelled``, ``automode-*``. Absent when the tool executed, including
    real failures.
    """

    userFeedback: str
    """Text the user gave when rejecting the call. Only with ``nonExecutionKind``."""

    mcpServer: McpServerInfo
    """The MCP server of an ``mcp__*`` tool, on a permission request."""


class TerminalInfoMeta(TypedDict):
    """Requests creation of a display-only terminal in the client.

    Sent on the initial ``tool_call`` of a shell tool, only to clients that
    declared ``terminal_output`` (see :data:`ClientCapabilitiesMeta`). Zed creates
    a display-only terminal identified by ``terminal_id``.
    """

    terminal_id: str
    """Identifier of the terminal; the tool call id."""

    cwd: NotRequired[str]
    """Working directory of the command. Sent by codex-acp, not by claude-agent-acp."""


class TerminalOutputMeta(TypedDict):
    """Terminal output for a display-only terminal.

    Under the ``terminal_output`` key each chunk replaces the output so far;
    under ``terminal_output_delta`` (for clients that declared it) chunks are
    appended. Zed only handles ``terminal_output``.
    """

    terminal_id: str
    """The terminal to write to. Must match a previous ``terminal_info``."""

    data: str
    """Terminal output (stdout and stderr combined)."""


class TerminalExitMeta(TypedDict):
    """Signals that the process of a display-only terminal has exited.

    Sent on the final ``tool_call_update`` of a shell tool.
    """

    terminal_id: str
    """The terminal that exited. Must match a previous ``terminal_info``."""

    exit_code: int | None
    """Process exit code, or ``None`` when the agent does not know it."""

    signal: str | None
    """Signal that terminated the process, or ``None``."""


class ToolUpdateMeta(TypedDict, total=False):
    """``_meta`` of ``tool_call`` and ``tool_call_update`` session updates.

    Defined as ``ToolUpdateMeta`` in claude-agent-acp's ``src/tool-calls/renderer.ts``;
    the terminal keys match codex-acp's.

    **Display-only terminal lifecycle** (shell tools, clients declaring
    ``terminal_output`` or ``terminal_output_delta``):

    1. ``tool_call`` with ``terminal_info``: client creates the terminal.
    2. ``tool_call_update`` with ``terminal_output`` / ``terminal_output_delta``:
       client shows the output.
    3. ``tool_call_update`` with ``terminal_exit``: client marks the process exited.

    This exists because these agents run commands themselves, while ACP v1
    expects the client to own terminals via ``terminal/create``. ACP v2 removes
    client terminals and adds agent-owned display terminals instead.
    """

    claudeCode: ClaudeCodeToolMeta
    """Claude Code-specific tool metadata."""

    is_mcp_tool_call: Literal[True]
    """Set on MCP tool calls (claude-agent-acp)."""

    terminal_info: TerminalInfoMeta
    """Create a display-only terminal (initial ``tool_call``)."""

    terminal_output: TerminalOutputMeta
    """Terminal output, replacing earlier output."""

    terminal_output_delta: TerminalOutputMeta
    """Terminal output, appended to earlier output."""

    terminal_exit: TerminalExitMeta
    """Process exit (final ``tool_call_update``)."""


# ---------------------------------------------------------------------------
# Zed tool call and command meta
# ---------------------------------------------------------------------------


class SubagentSessionInfoMeta(TypedDict):
    """Links a tool call to the subagent session it spawned.

    Used by Zed to navigate into subagent threads.
    """

    session_id: str
    """Session id of the spawned subagent session."""

    message_start_index: int
    """Index of the first message of the subagent's turn."""

    message_end_index: NotRequired[int | None]
    """Index of the subagent's returned message; omitted (Zed) or ``None`` while
    it is running."""


class ToolCallMeta(TypedDict, total=False):
    """Zed conventions for ``_meta`` on tool calls.

    Produced by Zed's native agent and read by Zed for any agent.
    """

    tool_name: str
    """Legacy tool name. Zed prefers the standard ACP ``name`` field and only
    falls back to this key."""

    subagent_session_info: SubagentSessionInfoMeta
    """Subagent session spawned by this tool call."""


class AvailableCommandMeta(TypedDict, total=False):
    """Zed convention for ``_meta`` on available (slash) commands."""

    command_category: Literal["native", "mcp"]
    """Source of the command, used to group the completion popup.

    Only Zed's native agent sets it; commands without it are grouped on their own.
    """


# ---------------------------------------------------------------------------
# Client capabilities meta  (_meta on ClientCapabilities, initialize request)
# ---------------------------------------------------------------------------


ClientCapabilitiesMeta = TypedDict(
    "ClientCapabilitiesMeta",
    {
        "terminal_output": bool,
        "terminal_output_delta": bool,
        "terminal-auth": bool,
        "subagent-transcript": bool,
    },
    total=False,
)
"""``_meta`` on :class:`~acp.schema.capabilities.ClientCapabilities`.

- ``terminal_output``: the client renders display-only terminals from
  :class:`ToolUpdateMeta` terminal keys. Without it, agents send shell output as
  plain content. Zed's v1 client sets it.
- ``terminal_output_delta``: like ``terminal_output``, but output arrives as
  appended ``terminal_output_delta`` chunks (claude-agent-acp, codex-acp).
- ``terminal-auth``: the client can run terminal login methods. Legacy form of
  the standard ``clientCapabilities.auth.terminal``; Zed's v1 client sets both.
- ``subagent-transcript``: the client wants subagent transcripts (claude-agent-acp).
"""


class AuthCapabilitiesMeta(TypedDict, total=False):
    """``_meta`` on :class:`~acp.schema.capabilities.AuthCapabilities`."""

    gateway: bool
    """The client supports the ``gateway`` auth method, which routes API calls
    through a custom Anthropic-protocol endpoint (claude-agent-acp, codex-acp)."""


# ---------------------------------------------------------------------------
# Auth method meta  (_meta on AuthMethod, initialize response)
# ---------------------------------------------------------------------------


class TerminalAuthValue(TypedDict):
    """Command a client runs in a terminal to authenticate."""

    label: str
    """Human-readable label of the login task."""

    command: str
    """Executable to run."""

    args: NotRequired[list[str]]
    """Arguments of the command (default: none)."""

    env: NotRequired[dict[str, str]]
    """Environment variables for the command (default: none)."""


TerminalAuthMethodMeta = TypedDict(
    "TerminalAuthMethodMeta",
    {"terminal-auth": TerminalAuthValue},
)
"""``_meta`` on an agent-type :class:`~acp.schema.common.AuthMethod` describing a
terminal login: the legacy form of a terminal auth method.

Zed runs the command for any agent's auth method that carries it, and builds one
itself for Gemini (``gemini /auth``).
"""


# ---------------------------------------------------------------------------
# Initialize response meta
# ---------------------------------------------------------------------------


class ClaudeCodeAgentCapabilitiesMeta(TypedDict, total=False):
    """``claudeCode`` entry of :class:`AgentCapabilitiesMeta`."""

    promptQueueing: bool
    """The agent queues prompts sent while a turn is running."""


class AgentCapabilitiesMeta(TypedDict, total=False):
    """``_meta`` on ``InitializeResponse.agentCapabilities``."""

    claudeCode: ClaudeCodeAgentCapabilitiesMeta
    """Claude Code feature flags (claude-agent-acp)."""

    authStatus: dict[str, Any]
    """Present (and empty) when the agent pushes its identity via the
    ``_auth/status_update`` extension notification (claude-agent-acp, codex-acp)."""


class SteeringSupport(TypedDict):
    """``steering`` entry of :class:`InitializeResponseMeta`."""

    supported: bool
    """The agent accepts the ``_session/steering`` extension request."""


class InitializeResponseMeta(TypedDict, total=False):
    """Top-level ``_meta`` on ``InitializeResponse`` (claude-agent-acp, codex-acp)."""

    steering: SteeringSupport
    """Steering support."""


# ---------------------------------------------------------------------------
# Session setup meta  (_meta on session/new, session/load, session/resume, session/fork)
# ---------------------------------------------------------------------------


class ClaudeCodeSessionOptions(TypedDict, total=False):
    """Claude Agent SDK ``Options`` forwarded via ``_meta.claudeCode.options``.

    Only JSON-serializable options are listed; options that take callbacks
    (``hooks``, ``canUseTool``, ``abortController``, ``stderr``, …) cannot be sent
    over ACP. Any other key is forwarded to the SDK as is. Types follow
    ``@anthropic-ai/claude-agent-sdk`` 0.3.326, the version claude-agent-acp
    v0.108.1 uses.

    The adapter **ignores** ``cwd``, ``includePartialMessages``, ``permissionMode``,
    ``executable``, ``pathToClaudeCodeExecutable`` and ``sessionId``, and
    **merges** ``env``, ``extraArgs``, ``mcpServers`` and ``disallowedTools`` with
    its own values.
    """

    resume: str
    """Session id of a previous Claude Code conversation to resume (``session/new``)."""

    model: str
    """Model to use."""

    fallbackModel: str
    """Model to use when the primary one is unavailable."""

    effort: Literal["low", "medium", "high", "xhigh", "max"] | int
    """Reasoning effort."""

    maxTurns: int
    """Maximum number of agentic turns."""

    maxBudgetUsd: float
    """Spending limit in USD."""

    maxThinkingTokens: int
    """Thinking token budget."""

    tools: list[str] | dict[str, str]
    """Built-in tools to expose: tool names, or ``{"type": "preset", "preset":
    "claude_code"}`` (the default). ``[]`` disables all built-in tools."""

    allowedTools: list[str]
    """Tools allowed without a permission prompt."""

    disallowedTools: list[str]
    """Tools to remove; merged with the adapter's own list."""

    additionalDirectories: list[str]
    """Extra directories Claude Code may access. Prefer the standard
    ``additionalDirectories`` field of the request."""

    mcpServers: dict[str, dict[str, Any]]
    """MCP server configs by name; merged with the request's ``mcpServers``."""

    env: dict[str, str]
    """Environment variables for the Claude Code process; merged."""

    extraArgs: dict[str, str | None]
    """Extra CLI flags (``None`` for flags without a value); merged."""

    allowDangerouslySkipPermissions: bool
    """``False`` removes ``bypassPermissions`` from the session (repeat it on
    ``session/load``). ``True`` cannot override the adapter's own restrictions."""

    forwardSubagentText: bool
    """Stream text produced by subagents to the client."""

    settingSources: list[Literal["user", "project", "local"]]
    """Which settings files Claude Code loads (adapter default: all three)."""

    promptSuggestions: bool
    """Predict the next prompt after each turn (adapter default: ``True``)."""

    strictMcpConfig: bool
    """Only use the given MCP servers, ignoring configured ones."""


class SDKMessageFilter(TypedDict):
    """Filter for raw SDK messages emitted as ``_claude/sdkMessage``."""

    type: str
    """SDK message type."""

    subtype: NotRequired[str]
    """SDK message subtype."""

    origin: NotRequired[str]
    """Origin kind of the message."""


class ClaudeCodeNewSessionMeta(TypedDict, total=False):
    """Claude Code-specific metadata nested under ``claudeCode``."""

    options: ClaudeCodeSessionOptions
    """Options forwarded to the Claude Agent SDK."""

    emitRawSDKMessages: bool | list[SDKMessageFilter]
    """Also send raw SDK messages as the ``_claude/sdkMessage`` extension
    notification: ``True`` for all, ``False`` (default) for none, or only
    messages matching at least one filter."""


class NewSessionMeta(TypedDict, total=False):
    """``_meta`` on session setup requests to claude-agent-acp.

    Applies to ``session/new``, ``session/load``, ``session/resume`` and
    ``session/fork``. Defined as ``NewSessionMeta`` in ``src/new-session-meta.ts``; the legacy keys
    are read in ``src/v1/acp-agent.ts``.
    """

    claudeCode: ClaudeCodeNewSessionMeta
    """Claude Code-specific session options."""

    additionalRoots: list[str]
    """Legacy: extra workspace roots. The standard ``additionalDirectories``
    field takes precedence."""

    systemPrompt: str | dict[str, Any]
    """Legacy: a full system prompt (string) or Claude Code preset options such as
    ``{"append": "..."}``. The standard ``systemPrompt`` field takes precedence."""

    disableBuiltInTools: bool
    """Legacy shorthand for ``claudeCode.options.tools = []``. An explicit
    ``tools`` option takes precedence."""


# ---------------------------------------------------------------------------
# Prompt and steering meta
# ---------------------------------------------------------------------------


class SteeringOptions(TypedDict, total=False):
    """``steering`` entry of :class:`SteeringRequestMeta`."""

    idleBehavior: Literal["promptRequired"]
    """Without a running turn, answer ``{"outcome": "promptRequired"}`` instead
    of starting a new turn."""


class SteeringRequestMeta(TypedDict, total=False):
    """``_meta`` on the ``_session/steering`` extension request.

    The request steers the running turn with more input instead of queueing a
    separate ``session/prompt``. Supported when
    :class:`InitializeResponseMeta` ``steering.supported`` is set.
    """

    steering: SteeringOptions
    """Steering options."""


class TokenCount(TypedDict):
    """Token counts of a turn or model (shape shared by claude-agent-acp and codex-acp)."""

    totalTokens: int
    """All tokens, including cache reads and writes."""

    inputTokens: int
    """Uncached input tokens."""

    cachedInputTokens: int
    """Cache reads."""

    cachedWriteTokens: NotRequired[int]
    """Cache writes (claude-agent-acp only)."""

    outputTokens: int
    """Output tokens."""

    reasoningOutputTokens: int
    """Reasoning tokens (always ``0`` for Claude, which counts them as output)."""


class ModelTokenCount(TypedDict):
    """Token counts of one model in :class:`Quota`."""

    model: str
    """Model id."""

    token_count: TokenCount
    """Tokens spent by this model."""


class Quota(TypedDict):
    """``quota`` entry of :class:`PromptResponseMeta`."""

    token_count: TokenCount
    """Tokens of the main agent loop."""

    model_usage: list[ModelTokenCount]
    """Tokens by model, including subagents."""


class PromptResponseMeta(TypedDict, total=False):
    """``_meta`` on the ``session/prompt`` response."""

    quota: Quota
    """Token usage of the turn."""


# ---------------------------------------------------------------------------
# Session update meta
# ---------------------------------------------------------------------------


class PromptSuggestion(TypedDict):
    """One predicted next prompt."""

    text: str
    """The predicted prompt."""


class PromptSuggestionsValue(TypedDict):
    """``promptSuggestions`` entry of :class:`SessionInfoUpdateMeta`."""

    suggestions: list[PromptSuggestion]
    """Predicted prompts; empty to clear an earlier prediction."""


class SessionInfoUpdateMeta(TypedDict, total=False):
    """``_meta`` on ``session_info_update`` notifications (claude-agent-acp)."""

    promptSuggestions: PromptSuggestionsValue
    """The prompt the agent expects the user to send next."""


UsageUpdateMeta = TypedDict(
    "UsageUpdateMeta",
    {
        "_claude/model": str,
        "_claude/rateLimit": dict[str, Any],
        "_claude/origin": dict[str, Any],
    },
    total=False,
)
"""``_meta`` on ``usage_update`` notifications (claude-agent-acp).

- ``_claude/model``: model of the last assistant message.
- ``_claude/rateLimit``: rate-limit info reported by the SDK.
- ``_claude/origin``: origin of the turn's result message.
"""


# ---------------------------------------------------------------------------
# Gateway authentication meta  (_meta on authenticate request)
# ---------------------------------------------------------------------------


class GatewayConfig(TypedDict):
    """Gateway configuration for routing API calls through a custom endpoint."""

    baseUrl: str
    """Base URL to send API calls to."""

    headers: dict[str, str]
    """Headers added to every API request."""


class GatewayAuthMeta(TypedDict):
    """``_meta`` on ``authenticate`` requests using the ``gateway`` method.

    Offered only to clients that declare :class:`AuthCapabilitiesMeta` ``gateway``.
    The agent maps the config to environment variables that override the
    default API endpoint.
    """

    gateway: GatewayConfig
    """Gateway routing configuration."""
