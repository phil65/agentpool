"""Claude storage converters."""

from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from clawd_code_sdk.models import (
    TextBlock as ClaudeTextBlock,
    ThinkingBlock as ClaudeThinkingBlock,
    ToolResultBlock as ClaudeToolResultBlock,
    ToolUseBlock as ClaudeToolUseBlock,
)
from clawd_code_sdk.storage.models import (
    ClaudeApiMessage,
    ClaudeAssistantEntry,
    ClaudeUsage,
    ClaudeUserEntry,
    ClaudeUserMessage,
)
from pydantic_ai import (
    ModelRequest,
    ModelResponse,
    RequestUsage,
    RunUsage,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agentpool.messaging import ChatMessage, TokenCost
from agentpool.utils.time_utils import parse_iso_timestamp


if TYPE_CHECKING:
    from datetime import datetime

    from clawd_code_sdk.storage.models import ClaudeJSONLEntry


def chat_message_to_entry(
    message: ChatMessage[str],
    session_id: str,
    cwd: str | None = None,
) -> ClaudeUserEntry | ClaudeAssistantEntry:
    """Convert a ChatMessage to a Claude JSONL entry."""
    timestamp = message.timestamp.isoformat().replace("+00:00", "Z")
    # Build entry based on role
    if message.role == "user":
        return ClaudeUserEntry(
            type="user",
            uuid=message.message_id,
            parent_uuid=message.parent_id,
            session_id=session_id,
            timestamp=timestamp,
            message=ClaudeUserMessage(role="user", content=message.content),
            cwd=cwd or "",
            version="agentpool",
            user_type="external",
            is_sidechain=False,
        )
    # Assistant message
    usage = ClaudeUsage(
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        cache_read_input_tokens=message.usage.cache_read_tokens,
        cache_creation_input_tokens=message.usage.cache_write_tokens,
    )
    assistant_msg = ClaudeApiMessage(
        model=message.model_name or "unknown",
        id=f"msg_{message.message_id[:20]}",
        role="assistant",
        content=[ClaudeTextBlock(type="text", text=message.content)],
        usage=usage,
    )
    return ClaudeAssistantEntry(
        type="assistant",
        uuid=message.message_id,
        parent_uuid=message.parent_id,
        session_id=session_id,
        timestamp=timestamp,
        message=assistant_msg,
        cwd=cwd or "",
        version="agentpool",
        user_type="external",
        is_sidechain=False,
    )


def extract_text_content(message: ClaudeApiMessage | ClaudeUserMessage) -> str:
    """Extract text content from a Claude message for display.

    Only extracts text and thinking blocks, not tool calls/results.
    """
    msg_content = message.content
    if isinstance(msg_content, str):
        return msg_content

    text_parts: list[str] = []
    for part in msg_content:
        match part:
            case ClaudeTextBlock(text=text) if text:
                text_parts.append(text)
            case ClaudeThinkingBlock(thinking=thinking) if thinking:
                # Include thinking in display content
                text_parts.append(f"<thinking>\n{thinking}\n</thinking>")
    return "\n".join(text_parts)


def normalize_model_name(model: str | None) -> str | None:
    """Normalize Claude model names to simple IDs.

    Claude storage uses full model names like 'claude-opus-4-5-20251101'
    but Claude Code agent exposes simple IDs like 'opus', 'sonnet', 'haiku'.
    This normalizes to simple IDs for consistency with get_available_models().
    """
    if model is None:
        return None
    model_lower = model.lower()
    if "opus" in model_lower:
        return "opus"
    if "sonnet" in model_lower:
        return "sonnet"
    if "haiku" in model_lower:
        return "haiku"
    # Return original if not a known Claude model
    return model


def entry_to_chat_message(
    entry: ClaudeJSONLEntry,
    session_id: str,
    tool_id_mapping: dict[str, str] | None = None,
) -> ChatMessage[str] | None:
    """Convert a Claude JSONL entry to a ChatMessage.

    Reconstructs pydantic-ai ModelRequest/ModelResponse objects and stores
    them in the messages field for full fidelity.

    Args:
        entry: The JSONL entry to convert
        session_id: ID for the conversation
        tool_id_mapping: Optional mapping from tool_call_id to tool_name
            for resolving tool names in ToolReturnPart

    Returns None for non-message entries (queue-operation, summary, etc.).
    """
    # Only handle user/assistant entries with messages
    if not isinstance(entry, (ClaudeUserEntry, ClaudeAssistantEntry)):
        return None

    message = entry.message
    timestamp = parse_iso_timestamp(entry.timestamp)
    # Extract display content (text only for UI)
    content = extract_text_content(message)
    # Build pydantic-ai message
    pydantic_message = build_pydantic_message(entry, message, timestamp, tool_id_mapping or {})
    # Extract token usage and cost
    cost_info = None
    model = None
    finish_reason = None
    input_tokens = 0
    output_tokens = 0
    if isinstance(entry, ClaudeAssistantEntry) and isinstance(message, ClaudeApiMessage):
        usage = message.usage
        input_tokens = (
            usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens
        )
        output_tokens = usage.output_tokens
        if input_tokens or output_tokens:
            cost_info = TokenCost(total_cost=Decimal(0))  # Claude doesn't store cost directly
        model = normalize_model_name(message.model)
        finish_reason = message.stop_reason

    return ChatMessage[str](
        content=content,
        session_id=session_id,
        role=entry.type,
        message_id=entry.uuid,
        name="claude" if isinstance(entry, ClaudeAssistantEntry) else None,
        model_name=model,
        cost_info=cost_info,
        usage=RunUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        timestamp=timestamp,
        parent_id=entry.parent_uuid,
        messages=[pydantic_message] if pydantic_message else [],
        provider_details={"finish_reason": finish_reason} if finish_reason else {},
    )


def build_pydantic_message(
    entry: ClaudeUserEntry | ClaudeAssistantEntry,
    message: ClaudeApiMessage | ClaudeUserMessage,
    timestamp: datetime,
    tool_id_mapping: dict[str, str],
) -> ModelRequest | ModelResponse | None:
    """Build a pydantic-ai ModelRequest or ModelResponse from Claude data.

    Args:
        entry: The entry being converted
        message: The message content
        timestamp: Parsed timestamp
        tool_id_mapping: Mapping from tool_call_id to tool_name
    """
    msg_content = message.content
    if isinstance(entry, ClaudeUserEntry):
        # Build ModelRequest with user prompt parts
        parts: list[UserPromptPart | ToolReturnPart] = []

        if isinstance(msg_content, str):
            parts.append(UserPromptPart(content=msg_content, timestamp=timestamp))
        else:
            for block in msg_content:
                match block:
                    case ClaudeTextBlock(text=text) if text:
                        parts.append(UserPromptPart(content=block.text, timestamp=timestamp))
                    case ClaudeToolResultBlock(tool_use_id=tool_use_id) if tool_use_id:
                        # Reconstruct tool return - look up tool name from mapping
                        tool_content = block.extract_text()
                        tool_name = tool_id_mapping.get(block.tool_use_id, "")
                        parts.append(
                            ToolReturnPart(
                                tool_name=tool_name,
                                content=tool_content,
                                tool_call_id=block.tool_use_id,
                                timestamp=timestamp,
                            )
                        )

        return ModelRequest(parts=parts, timestamp=timestamp) if parts else None

    # Build ModelResponse for assistant
    if not isinstance(message, ClaudeApiMessage):
        return None

    resp_parts: list[TextPart | ToolCallPart | ThinkingPart] = []
    usage = RequestUsage(
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        cache_read_tokens=message.usage.cache_read_input_tokens,
        cache_write_tokens=message.usage.cache_creation_input_tokens,
    )

    if isinstance(msg_content, str):
        resp_parts.append(TextPart(content=msg_content))
    else:
        for block in msg_content:
            match block:
                case ClaudeTextBlock(text=text) if text:
                    resp_parts.append(TextPart(content=text))
                case ClaudeThinkingBlock(thinking=thinking, signature=signature) if thinking:
                    resp_parts.append(ThinkingPart(content=thinking, signature=signature))
                case ClaudeToolUseBlock(id=block_id, name=name) if block_id and name:
                    args = cast(dict[str, Any], block.input or {})
                    part = ToolCallPart(tool_name=block.name, args=args, tool_call_id=block.id)
                    resp_parts.append(part)

    if not resp_parts:
        return None
    model = normalize_model_name(message.model)
    return ModelResponse(parts=resp_parts, usage=usage, model_name=model, timestamp=timestamp)


# def convert_to_pydantic_ai(
#     entries: list[ClaudeCodeEntry],
#     *,
#     include_sidechains: bool = False,
#     follow_parent_chain: bool = True,
# ) -> list[ModelRequest | ModelResponse]:
#     """Convert Claude Code entries to pydantic-ai message format.

#     Args:
#         entries: List of Claude Code history entries
#         include_sidechains: If True, include sidechain (forked) messages
#         follow_parent_chain: If True (default), reconstruct conversation order
#             by following parentUuid links. If False, use file order.

#     Returns:
#         List of ModelRequest and ModelResponse objects
#     """
#     from pydantic_ai import ModelRequest, ModelResponse

#     # Optionally reconstruct proper conversation order
#     conversation: list[ClaudeCodeEntry] | list[ClaudeCodeMessageEntry]
#     if follow_parent_chain:
#         conversation = get_main_conversation(entries, include_sidechains=include_sidechains)
#     else:
#         conversation = entries
#     from pydantic_ai import (
#         TextPart,
#         ThinkingPart,
#         ToolCallPart,
#         ToolReturnPart,
#         UserPromptPart,
#     )

#     messages: list[ModelRequest | ModelResponse] = []

#     for entry in conversation:
#         match entry:
#             case ClaudeCodeUserEntry():
#                 parts: list[Any] = []
#                 metadata = {
#                     "uuid": entry.uuid,
#                     "timestamp": entry.timestamp.isoformat(),
#                     "sessionId": entry.session_id,
#                     "cwd": entry.cwd,
#                     "gitBranch": entry.git_branch,
#                     "isSidechain": entry.is_sidechain,
#                 }

#                 content = entry.message.content
#                 if isinstance(content, str):
#                     parts.append(UserPromptPart(content=content))
#                 else:
#                     for block in content:
#                         match block:
#                             case ClaudeCodeTextContent():
#                                 parts.append(UserPromptPart(content=block.text))
#                             case ClaudeCodeToolResultContent():
#                                 # Extract text from tool result content
#                                 if isinstance(block.content, str):
#                                     result_content = block.content
#                                 else:
#                                     result_content = "\n".join(
#                                         c.text
#                                         for c in block.content
#                                         if isinstance(c, ClaudeCodeTextContent)
#                                     )
#                                 parts.append(
#                                     ToolReturnPart(
#                                         tool_name="",  # Not available in history
#                                         content=result_content,
#                                         tool_call_id=block.tool_use_id,
#                                     )
#                                 )

#                 if parts:
#                     messages.append(ModelRequest(parts=parts, metadata=metadata))

#             case ClaudeCodeAssistantEntry():
#                 parts = []
#                 metadata = {
#                     "uuid": entry.uuid,
#                     "timestamp": entry.timestamp.isoformat(),
#                     "sessionId": entry.session_id,
#                     "requestId": entry.request_id,
#                     "cwd": entry.cwd,
#                     "gitBranch": entry.git_branch,
#                     "isSidechain": entry.is_sidechain,
#                 }

#                 for block in entry.message.content:
#                     match block:
#                         case ClaudeCodeTextContent():
#                             parts.append(TextPart(content=block.text))
#                         case ClaudeCodeToolUseContent():
#                             parts.append(
#                                 ToolCallPart(
#                                     tool_name=block.name,
#                                     args=block.input,
#                                     tool_call_id=block.id,
#                                 )
#                             )
#                         case ClaudeCodeThinkingContent():
#                             parts.append(ThinkingPart(content=block.thinking))

#                 if parts:
#                     messages.append(
#                         ModelResponse(
#                             parts=parts,
#                             model_name=entry.message.model,
#                             provider_response_id=entry.message.id,
#                             metadata=metadata,
#                         )
#                     )

#             case ClaudeCodeSummary():
#                 # Summaries can be added as system context if needed
#                 metadata = {
#                     "uuid": entry.uuid,
#                     "timestamp": entry.timestamp.isoformat(),
#                     "sessionId": entry.session_id,
#                     "type": "summary",
#                 }
#                 messages.append(
#                     ModelRequest(
#                         parts=[UserPromptPart(content=f"[Summary]: {entry.summary}")],
#                         metadata=metadata,
#                     )
#                 )

#             case ClaudeCodeQueueOperation():
#                 # Skip queue operations - they're metadata, not messages
#                 pass

#     return messages
