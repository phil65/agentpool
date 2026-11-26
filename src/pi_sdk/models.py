"""Pydantic models for a Python stdio client to pi's RPC interface.

Based on:
- packages/ai/src/types.ts (core LLM types)
- packages/agent/src/types.ts (agent loop types)
- packages/coding-agent/src/modes/rpc/rpc-client.ts (RPC client API)
- packages/coding-agent/src/core/agent-session.ts (session types)
"""

import sys
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, TypeAdapter
from pydantic.alias_generators import to_camel


IS_DEV = "pytest" in sys.modules


# =============================================================================
# Base Model
# =============================================================================


class PiBaseModel(BaseModel):
    """Base model for all pi RPC Pydantic models.

    Automatically generates camelCase aliases from snake_case field names,
    matching the TypeScript/JSON wire format.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        extra="forbid" if IS_DEV else "ignore",
        defer_build=True,
        use_attribute_docstrings=True,
    )


# =============================================================================
# Enums / Literal Types
# =============================================================================

KnownApi = Literal[
    "openai-completions",
    "mistral-conversations",
    "openai-responses",
    "azure-openai-responses",
    "openai-codex-responses",
    "anthropic-messages",
    "bedrock-converse-stream",
    "google-generative-ai",
    "google-gemini-cli",
    "google-vertex",
]

Api = str  # KnownApi | arbitrary string

KnownProvider = Literal[
    "amazon-bedrock",
    "anthropic",
    "google",
    "google-gemini-cli",
    "google-antigravity",
    "google-vertex",
    "openai",
    "azure-openai-responses",
    "openai-codex",
    "github-copilot",
    "xai",
    "groq",
    "cerebras",
    "openrouter",
    "vercel-ai-gateway",
    "zai",
    "mistral",
    "minimax",
    "minimax-cn",
    "huggingface",
    "opencode",
    "opencode-go",
    "kimi-coding",
]

Provider = str  # KnownProvider | arbitrary string

StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]

CacheRetention = Literal["none", "short", "long"]

Transport = Literal["sse", "websocket", "auto"]

ToolExecutionMode = Literal["sequential", "parallel"]

InputType = Literal["text", "image"]

SteeringMode = Literal["all", "one-at-a-time"]


# =============================================================================
# Content Types
# =============================================================================


class TextContent(PiBaseModel):
    """Text content."""

    type: Literal["text"] = "text"
    text: str
    text_signature: str | None = None


class ThinkingContent(PiBaseModel):
    """Thinking content."""

    type: Literal["thinking"] = "thinking"
    thinking: str
    thinking_signature: str | None = None
    redacted: bool | None = None


class ImageContent(PiBaseModel):
    """Image content."""

    type: Literal["image"] = "image"
    data: str  # base64 encoded
    mime_type: str


class ToolCall(PiBaseModel):
    """Tool call content."""

    type: Literal["toolCall"] = "toolCall"
    id: str
    name: str
    arguments: dict[str, Any]


# =============================================================================
# Usage / Cost
# =============================================================================


class UsageCost(PiBaseModel):
    """Usage cost."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    total: float


class Usage(PiBaseModel):
    """Usage."""

    input: int
    output: int
    cache_read: int
    cache_write: int
    total_tokens: int
    cost: UsageCost


# =============================================================================
# Messages
# =============================================================================


class UserMessage(PiBaseModel):
    """User message."""

    role: Literal["user"] = "user"
    content: str | list[TextContent | ImageContent]
    timestamp: int
    """Unix timestamp in milliseconds."""


class AssistantMessage(PiBaseModel):
    """Assistant message."""

    role: Literal["assistant"] = "assistant"
    content: list[TextContent | ThinkingContent | ToolCall]
    api: str
    provider: str
    model: str
    response_id: str | None = None
    usage: Usage
    stop_reason: StopReason
    error_message: str | None = None
    timestamp: int


class ToolResultMessage(PiBaseModel):
    """Tool result message."""

    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str
    tool_name: str
    content: list[TextContent | ImageContent]
    details: Any | None = None
    is_error: bool
    timestamp: int


Message = Annotated[
    UserMessage | AssistantMessage | ToolResultMessage,
    Discriminator("role"),
]


# =============================================================================
# Custom Messages (coding-agent specific)
# =============================================================================


class BashExecutionMessage(PiBaseModel):
    """Bash execution message."""

    role: Literal["bashExecution"] = "bashExecution"
    command: str
    output: str
    exit_code: int
    cancelled: bool
    truncated: bool
    full_output_path: str | None = None
    timestamp: int
    exclude_from_context: bool | None = None


class CustomMessage(PiBaseModel):
    """Custom message."""

    role: Literal["custom"] = "custom"
    custom_type: str
    content: Any
    display: Any | None = None
    details: Any | None = None
    timestamp: int


AgentMessage = Annotated[
    UserMessage | AssistantMessage | ToolResultMessage | BashExecutionMessage | CustomMessage,
    Discriminator("role"),
]

AgentMessageAdapter: TypeAdapter[AgentMessage] = TypeAdapter(AgentMessage)


# =============================================================================
# Tool Definition
# =============================================================================


class Tool(PiBaseModel):
    """Tool definition."""

    name: str
    description: str
    parameters: dict[str, Any]
    """JSON Schema (TypeBox TSchema)."""


# =============================================================================
# Model
# =============================================================================


class ModelCost(PiBaseModel):
    """Model cost per million tokens."""

    input: float
    """$/million tokens."""
    output: float
    cache_read: float
    cache_write: float


class Model(PiBaseModel):
    """Model configuration."""

    id: str
    name: str
    api: str
    provider: str
    base_url: str
    reasoning: bool
    input: list[InputType]
    cost: ModelCost
    context_window: int
    max_tokens: int
    headers: dict[str, str] | None = None
    compat: dict[str, Any] | None = None


# =============================================================================
# Assistant Message Events (streaming protocol)
# =============================================================================


class EventStart(PiBaseModel):
    """Stream start event."""

    type: Literal["start"] = "start"
    partial: AssistantMessage


class EventTextStart(PiBaseModel):
    """Text content start event."""

    type: Literal["text_start"] = "text_start"
    content_index: int
    partial: AssistantMessage


class EventTextDelta(PiBaseModel):
    """Text content delta event."""

    type: Literal["text_delta"] = "text_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class EventTextEnd(PiBaseModel):
    """Text content end event."""

    type: Literal["text_end"] = "text_end"
    content_index: int
    content: str
    partial: AssistantMessage


class EventThinkingStart(PiBaseModel):
    """Thinking content start event."""

    type: Literal["thinking_start"] = "thinking_start"
    content_index: int
    partial: AssistantMessage


class EventThinkingDelta(PiBaseModel):
    """Thinking content delta event."""

    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class EventThinkingEnd(PiBaseModel):
    """Thinking content end event."""

    type: Literal["thinking_end"] = "thinking_end"
    content_index: int
    content: str
    partial: AssistantMessage


class EventToolcallStart(PiBaseModel):
    """Tool call start event."""

    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int
    partial: AssistantMessage


class EventToolcallDelta(PiBaseModel):
    """Tool call delta event."""

    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class EventToolcallEnd(PiBaseModel):
    """Tool call end event."""

    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage


class EventDone(PiBaseModel):
    """Stream done event."""

    type: Literal["done"] = "done"
    reason: Literal["stop", "length", "toolUse"]
    message: AssistantMessage


class EventError(PiBaseModel):
    """Stream error event."""

    type: Literal["error"] = "error"
    reason: Literal["error", "aborted"]
    error: AssistantMessage


AssistantMessageEvent = Annotated[
    EventStart
    | EventTextStart
    | EventTextDelta
    | EventTextEnd
    | EventThinkingStart
    | EventThinkingDelta
    | EventThinkingEnd
    | EventToolcallStart
    | EventToolcallDelta
    | EventToolcallEnd
    | EventDone
    | EventError,
    Discriminator("type"),
]


# =============================================================================
# Agent Events (emitted over RPC)
# =============================================================================


class AgentStartEvent(PiBaseModel):
    """Agent start event."""

    type: Literal["agent_start"] = "agent_start"


class AgentEndEvent(PiBaseModel):
    """Agent end event."""

    type: Literal["agent_end"] = "agent_end"
    messages: list[AgentMessage]


class TurnStartEvent(PiBaseModel):
    """Turn start event."""

    type: Literal["turn_start"] = "turn_start"


class TurnEndEvent(PiBaseModel):
    """Turn end event."""

    type: Literal["turn_end"] = "turn_end"
    message: AgentMessage
    tool_results: list[ToolResultMessage]


class MessageStartEvent(PiBaseModel):
    """Message start event."""

    type: Literal["message_start"] = "message_start"
    message: AgentMessage


class MessageUpdateEvent(PiBaseModel):
    """Message update event."""

    type: Literal["message_update"] = "message_update"
    message: AgentMessage
    assistant_message_event: AssistantMessageEvent


class MessageEndEvent(PiBaseModel):
    """Message end event."""

    type: Literal["message_end"] = "message_end"
    message: AgentMessage


class ToolExecutionStartEvent(PiBaseModel):
    """Tool execution start event."""

    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str
    tool_name: str
    args: Any


class ToolExecutionUpdateEvent(PiBaseModel):
    """Tool execution update event."""

    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str
    tool_name: str
    args: Any
    partial_result: Any


class ToolExecutionEndEvent(PiBaseModel):
    """Tool execution end event."""

    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str
    tool_name: str
    result: Any
    is_error: bool


AgentEvent = Annotated[
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent,
    Discriminator("type"),
]

AgentEventAdapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)


# =============================================================================
# Session-specific Events (AgentSessionEvent extends AgentEvent)
# =============================================================================


class QueueUpdateEvent(PiBaseModel):
    """Queue update event."""

    type: Literal["queue_update"] = "queue_update"
    steering: list[str]
    follow_up: list[str]


class CompactionStartEvent(PiBaseModel):
    """Compaction start event."""

    type: Literal["compaction_start"] = "compaction_start"
    reason: Literal["manual", "threshold", "overflow"]


class CompactionEndEvent(PiBaseModel):
    """Compaction end event."""

    type: Literal["compaction_end"] = "compaction_end"
    reason: Literal["manual", "threshold", "overflow"]
    result: CompactionResult | None = None
    aborted: bool
    will_retry: bool
    error_message: str | None = None


class AutoRetryStartEvent(PiBaseModel):
    """Auto-retry start event."""

    type: Literal["auto_retry_start"] = "auto_retry_start"
    attempt: int
    max_attempts: int
    delay_ms: int
    error_message: str


class AutoRetryEndEvent(PiBaseModel):
    """Auto-retry end event."""

    type: Literal["auto_retry_end"] = "auto_retry_end"
    success: bool
    attempt: int
    final_error: str | None = None


AgentSessionEvent = Annotated[
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
    | QueueUpdateEvent
    | CompactionStartEvent
    | CompactionEndEvent
    | AutoRetryStartEvent
    | AutoRetryEndEvent,
    Discriminator("type"),
]

AgentSessionEventAdapter: TypeAdapter[AgentSessionEvent] = TypeAdapter(AgentSessionEvent)


# =============================================================================
# RPC Session State
# =============================================================================


class ContextUsage(PiBaseModel):
    """Context window usage."""

    tokens: int | None
    context_window: int
    percent: float | None


class RpcSessionState(PiBaseModel):
    """RPC session state."""

    model: Model | None = None
    thinking_level: ThinkingLevel
    is_streaming: bool
    is_compacting: bool
    steering_mode: SteeringMode
    follow_up_mode: SteeringMode
    session_file: str | None = None
    session_id: str
    session_name: str | None = None
    auto_compaction_enabled: bool
    message_count: int
    pending_message_count: int


# =============================================================================
# Session Stats
# =============================================================================


class TokenStats(PiBaseModel):
    """Token statistics."""

    input: int
    output: int
    cache_read: int
    cache_write: int
    total: int


SourceScope = Literal["user", "project", "temporary"]
SourceOrigin = Literal["package", "top-level"]


class SourceInfo(PiBaseModel):
    """Source info for extension/resource origin."""

    path: str
    source: str
    scope: SourceScope
    origin: SourceOrigin
    base_dir: str | None = None


class SessionStats(PiBaseModel):
    """Session statistics."""

    session_file: str | None = None
    session_id: str
    user_messages: int
    assistant_messages: int
    tool_calls: int
    tool_results: int
    total_messages: int
    tokens: TokenStats
    cost: float
    context_usage: ContextUsage | None = None


# =============================================================================
# Bash Result
# =============================================================================


class BashResult(PiBaseModel):
    """Bash command result."""

    output: str
    exit_code: int | None = None
    cancelled: bool = False
    truncated: bool = False
    full_output_path: str | None = None


# =============================================================================
# Compaction Result
# =============================================================================


class CompactionResult(PiBaseModel):
    """Compaction result."""

    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: Any | None = None


# =============================================================================
# Model Info (from getAvailableModels)
# =============================================================================


# ModelInfo is just Model — get_available_models returns full Model objects
ModelInfo = Model


# =============================================================================
# RPC Slash Command
# =============================================================================


class RpcSlashCommand(PiBaseModel):
    """RPC slash command."""

    name: str
    description: str | None = None
    source: Literal["extension", "prompt", "skill"]
    source_info: SourceInfo


# =============================================================================
# RPC Commands (client -> agent via stdin)
# =============================================================================


class RpcPromptCommand(PiBaseModel):
    """Prompt command."""

    type: Literal["prompt"] = "prompt"
    id: str
    message: str
    images: list[ImageContent] | None = None
    streaming_behavior: Literal["steer", "followUp"] | None = None


class RpcSteerCommand(PiBaseModel):
    """Steer command."""

    type: Literal["steer"] = "steer"
    id: str
    message: str
    images: list[ImageContent] | None = None


class RpcFollowUpCommand(PiBaseModel):
    """Follow-up command."""

    type: Literal["follow_up"] = "follow_up"
    id: str
    message: str
    images: list[ImageContent] | None = None


class RpcAbortCommand(PiBaseModel):
    """Abort command."""

    type: Literal["abort"] = "abort"
    id: str


class RpcNewSessionCommand(PiBaseModel):
    """New session command."""

    type: Literal["new_session"] = "new_session"
    id: str
    parent_session: str | None = None


class RpcGetStateCommand(PiBaseModel):
    """Get state command."""

    type: Literal["get_state"] = "get_state"
    id: str


class RpcSetModelCommand(PiBaseModel):
    """Set model command."""

    type: Literal["set_model"] = "set_model"
    id: str
    provider: str
    model_id: str


class RpcCycleModelCommand(PiBaseModel):
    """Cycle model command."""

    type: Literal["cycle_model"] = "cycle_model"
    id: str


class RpcGetAvailableModelsCommand(PiBaseModel):
    """Get available models command."""

    type: Literal["get_available_models"] = "get_available_models"
    id: str


class RpcSetThinkingLevelCommand(PiBaseModel):
    """Set thinking level command."""

    type: Literal["set_thinking_level"] = "set_thinking_level"
    id: str
    level: ThinkingLevel


class RpcCycleThinkingLevelCommand(PiBaseModel):
    """Cycle thinking level command."""

    type: Literal["cycle_thinking_level"] = "cycle_thinking_level"
    id: str


class RpcSetSteeringModeCommand(PiBaseModel):
    """Set steering mode command."""

    type: Literal["set_steering_mode"] = "set_steering_mode"
    id: str
    mode: SteeringMode


class RpcSetFollowUpModeCommand(PiBaseModel):
    """Set follow-up mode command."""

    type: Literal["set_follow_up_mode"] = "set_follow_up_mode"
    id: str
    mode: SteeringMode


class RpcCompactCommand(PiBaseModel):
    """Compact command."""

    type: Literal["compact"] = "compact"
    id: str
    custom_instructions: str | None = None


class RpcSetAutoCompactionCommand(PiBaseModel):
    """Set auto-compaction command."""

    type: Literal["set_auto_compaction"] = "set_auto_compaction"
    id: str
    enabled: bool


class RpcSetAutoRetryCommand(PiBaseModel):
    """Set auto-retry command."""

    type: Literal["set_auto_retry"] = "set_auto_retry"
    id: str
    enabled: bool


class RpcAbortRetryCommand(PiBaseModel):
    """Abort retry command."""

    type: Literal["abort_retry"] = "abort_retry"
    id: str


class RpcBashCommand(PiBaseModel):
    """Bash command."""

    type: Literal["bash"] = "bash"
    id: str
    command: str


class RpcAbortBashCommand(PiBaseModel):
    """Abort bash command."""

    type: Literal["abort_bash"] = "abort_bash"
    id: str


class RpcGetSessionStatsCommand(PiBaseModel):
    """Get session stats command."""

    type: Literal["get_session_stats"] = "get_session_stats"
    id: str


class RpcExportHtmlCommand(PiBaseModel):
    """Export HTML command."""

    type: Literal["export_html"] = "export_html"
    id: str
    output_path: str | None = None


class RpcSwitchSessionCommand(PiBaseModel):
    """Switch session command."""

    type: Literal["switch_session"] = "switch_session"
    id: str
    session_path: str


class RpcForkCommand(PiBaseModel):
    """Fork command."""

    type: Literal["fork"] = "fork"
    id: str
    entry_id: str


class RpcGetForkMessagesCommand(PiBaseModel):
    """Get fork messages command."""

    type: Literal["get_fork_messages"] = "get_fork_messages"
    id: str


class RpcGetLastAssistantTextCommand(PiBaseModel):
    """Get last assistant text command."""

    type: Literal["get_last_assistant_text"] = "get_last_assistant_text"
    id: str


class RpcSetSessionNameCommand(PiBaseModel):
    """Set session name command."""

    type: Literal["set_session_name"] = "set_session_name"
    id: str
    name: str


class RpcGetMessagesCommand(PiBaseModel):
    """Get messages command."""

    type: Literal["get_messages"] = "get_messages"
    id: str


class RpcGetCommandsCommand(PiBaseModel):
    """Get commands command."""

    type: Literal["get_commands"] = "get_commands"
    id: str


RpcCommand = (
    RpcPromptCommand
    | RpcSteerCommand
    | RpcFollowUpCommand
    | RpcAbortCommand
    | RpcNewSessionCommand
    | RpcGetStateCommand
    | RpcSetModelCommand
    | RpcCycleModelCommand
    | RpcGetAvailableModelsCommand
    | RpcSetThinkingLevelCommand
    | RpcCycleThinkingLevelCommand
    | RpcSetSteeringModeCommand
    | RpcSetFollowUpModeCommand
    | RpcCompactCommand
    | RpcSetAutoCompactionCommand
    | RpcSetAutoRetryCommand
    | RpcAbortRetryCommand
    | RpcBashCommand
    | RpcAbortBashCommand
    | RpcGetSessionStatsCommand
    | RpcExportHtmlCommand
    | RpcSwitchSessionCommand
    | RpcForkCommand
    | RpcGetForkMessagesCommand
    | RpcGetLastAssistantTextCommand
    | RpcSetSessionNameCommand
    | RpcGetMessagesCommand
    | RpcGetCommandsCommand
)


# =============================================================================
# RPC Responses (agent -> client via stdout)
# =============================================================================


class RpcSuccessResponse(PiBaseModel):
    """RPC success response."""

    type: Literal["response"] = "response"
    id: str
    success: Literal[True] = True
    data: Any | None = None


class RpcErrorResponse(PiBaseModel):
    """RPC error response."""

    type: Literal["response"] = "response"
    id: str
    success: Literal[False] = False
    error: str


RpcResponse = RpcSuccessResponse | RpcErrorResponse


# =============================================================================
# Specific Response Data Types (returned inside RpcSuccessResponse.data)
# =============================================================================


class NewSessionData(PiBaseModel):
    """New session response data."""

    cancelled: bool


# set_model returns a full Model object
SetModelData = Model


class CycleModelData(PiBaseModel):
    """Cycle model response data."""

    model: Model
    thinking_level: ThinkingLevel
    is_scoped: bool


class AvailableModelsData(PiBaseModel):
    """Available models response data."""

    models: list[ModelInfo]


class CycleThinkingLevelData(PiBaseModel):
    """Cycle thinking level response data."""

    level: ThinkingLevel


class ExportHtmlData(PiBaseModel):
    """Export HTML response data."""

    path: str


class SwitchSessionData(PiBaseModel):
    """Switch session response data."""

    cancelled: bool


class ForkMessageEntry(PiBaseModel):
    """Fork message entry."""

    entry_id: str
    text: str


class ForkData(PiBaseModel):
    """Fork response data."""

    text: str
    cancelled: bool


class ForkMessagesData(PiBaseModel):
    """Fork messages response data."""

    messages: list[ForkMessageEntry]


class LastAssistantTextData(PiBaseModel):
    """Last assistant text response data."""

    text: str | None


class MessagesData(PiBaseModel):
    """Messages response data."""

    messages: list[AgentMessage]


class CommandsData(PiBaseModel):
    """Commands data."""

    commands: list[RpcSlashCommand]


class SessionStatsData(PiBaseModel):
    """Wraps SessionStats in the RPC response."""

    stats: SessionStats


# =============================================================================
# Extension UI Events (agent -> client via stdout)
# =============================================================================

ExtensionUIMethod = Literal[
    "select",
    "confirm",
    "input",
    "editor",
    "notify",
    "setStatus",
    "setWidget",
    "setTitle",
    "set_editor_text",
]


class ExtensionUIRequest(PiBaseModel):
    """Extension UI request event emitted when an extension needs user input.

    Fields vary by method. All methods include type, id, and method.
    """

    type: Literal["extension_ui_request"] = "extension_ui_request"
    id: str
    method: ExtensionUIMethod
    title: str | None = None
    message: str | None = None
    options: list[str] | None = None
    placeholder: str | None = None
    prefill: str | None = None
    timeout: int | None = None
    notify_type: Literal["info", "warning", "error"] | None = None
    status_key: str | None = None
    status_text: str | None = None
    widget_key: str | None = None
    widget_lines: list[str] | None = None
    widget_placement: Literal["aboveEditor", "belowEditor"] | None = None
    text: str | None = None


class ExtensionUIResponseValue(PiBaseModel):
    """Extension UI response with a string value (select/input/editor)."""

    type: Literal["extension_ui_response"] = "extension_ui_response"
    id: str
    value: str


class ExtensionUIResponseConfirm(PiBaseModel):
    """Extension UI response for confirm dialogs."""

    type: Literal["extension_ui_response"] = "extension_ui_response"
    id: str
    confirmed: bool


class ExtensionUIResponseCancel(PiBaseModel):
    """Extension UI response for cancelled requests."""

    type: Literal["extension_ui_response"] = "extension_ui_response"
    id: str
    cancelled: Literal[True] = True


ExtensionUIResponse = (
    ExtensionUIResponseValue | ExtensionUIResponseConfirm | ExtensionUIResponseCancel
)
