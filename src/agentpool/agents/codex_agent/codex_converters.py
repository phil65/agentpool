"""Convert between Codex and AgentPool types.

Provides converters for:
- Event conversion (Codex streaming events -> AgentPool events)
- MCP server configs (Native configs -> Codex types)
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, assert_never, overload

from codexed.models import (
    ThreadItemAgentMessage,
    ThreadItemCollabAgentToolCall,
    ThreadItemContextCompaction,
    ThreadItemDynamicToolCall,
    ThreadItemEnteredReviewMode,
    ThreadItemExitedReviewMode,
    ThreadItemPlan,
    ThreadItemReasoning,
    ThreadItemUserMessage,
    ThreadItemWebSearch,
)
from pydantic_ai import (
    BinaryContent,
    BuiltinToolCallPart,
    CachePoint,
    FileUrl,
    ImageUrl,
    ModelRequest,
    ModelResponse,
    RequestUsage,
    RunUsage,
    TextContent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    UploadedFile,
    UserContent,
    UserPromptPart,
)

from agentpool.messaging import ChatMessage
from agentpool.sessions import SessionData
from agentpool.utils.pydantic_ai_helpers import get_builtin_tool_parts


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from codexed.models import (
        HttpMcpServer,
        InputModality,
        McpServerConfig,
        Model,
        StdioMcpServer,
        Thread,
        ThreadItem,
        TokenUsageBreakdown,
        Turn,
        TurnStatus,
        UserInput,
    )
    from pydantic_ai import FinishReason
    from tokonomics.model_discovery.model_info import Modality, ModelInfo as TokoModelInfo

    from agentpool_config.mcp_server import (
        MCPServerConfig,
        SSEMCPServerConfig,
        StdioMCPServerConfig,
        StreamableHTTPMCPServerConfig,
    )


_MODALITY_MAP: dict[InputModality, Modality] = {"text": "text", "image": "image"}


def to_finish_reason(status: TurnStatus) -> FinishReason:
    """Convert Codex TurnStatusValue to pydantic-ai FinishReason."""
    match status:
        case "completed":
            return "stop"
        case "interrupted":
            return "stop"
        case "failed":
            return "error"
        case "inProgress":
            return "stop"


def to_run_usage(usage: TokenUsageBreakdown) -> RunUsage:
    return RunUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cached_input_tokens,
    )


def to_request_usage(usage: TokenUsageBreakdown) -> RequestUsage:
    return RequestUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cached_input_tokens,
    )


@overload
def mcp_config_to_codex(config: StdioMCPServerConfig) -> tuple[str, StdioMcpServer]: ...


@overload
def mcp_config_to_codex(config: SSEMCPServerConfig) -> tuple[str, HttpMcpServer]: ...


@overload
def mcp_config_to_codex(
    config: StreamableHTTPMCPServerConfig,
) -> tuple[str, HttpMcpServer]: ...


@overload
def mcp_config_to_codex(config: MCPServerConfig) -> tuple[str, McpServerConfig]: ...


def mcp_config_to_codex(config: MCPServerConfig) -> tuple[str, McpServerConfig]:
    """Convert native MCPServerConfig to (name, Codex McpServerConfig) tuple.

    Args:
        config: Native MCP server configuration

    Returns:
        Tuple of (server name, Codex-compatible MCP server configuration)
    """
    from codexed.models import HttpMcpServer, StdioMcpServer

    from agentpool_config.mcp_server import (
        SSEMCPServerConfig,
        StdioMCPServerConfig,
        StreamableHTTPMCPServerConfig,
    )

    # Name should not be None by the time we use it
    server_name = config.name or f"server_{id(config)}"
    match config:
        case StdioMCPServerConfig(command=command, args=args, env=env, enabled=enabled):
            stdio_server = StdioMcpServer(command=command, args=args, env=env, enabled=enabled)
            return (server_name, stdio_server)

        case SSEMCPServerConfig(url=url, enabled=enabled):
            # Codex uses HTTP transport for SSE
            # SSE config just has URL, no separate auth fields
            return (server_name, HttpMcpServer(url=str(url), enabled=enabled))

        case StreamableHTTPMCPServerConfig(headers=headers, url=url, enabled=enabled):
            # StreamableHTTP has headers field
            return (server_name, HttpMcpServer(url=str(url), http_headers=headers, enabled=enabled))

        case _ as unreachable:
            raise assert_never(unreachable)


def to_model_info(model_data: Model, provider: str = "openai") -> TokoModelInfo:
    from tokonomics.model_discovery.model_info import ModelInfo as TokoModelInfo

    model_id = model_data.model or model_data.id
    return TokoModelInfo(
        id=model_id,
        name=model_data.display_name or model_data.id,
        provider=provider,
        description=model_data.description or None,
        id_override=model_id,
        input_modalities={_MODALITY_MAP[m] for m in model_data.input_modalities or []},
        metadata={
            k: v
            for k, v in {
                "hidden": model_data.hidden or None,
                "is_default": model_data.is_default or None,
                "upgrade": model_data.upgrade,
                "supports_personality": model_data.supports_personality or None,
            }.items()
            if v is not None
        },
    )


def to_session_data(thread_data: Thread, agent_name: str, cwd: str | None) -> SessionData:
    created_at = datetime.fromtimestamp(thread_data.created_at, tz=UTC)
    return SessionData(
        session_id=thread_data.id,
        agent_name=agent_name,
        cwd=thread_data.cwd or cwd,
        created_at=created_at,
        last_active=created_at,  # Codex doesn't track separate last_active
        metadata={"title": thread_data.preview} if thread_data.preview else {},
    )


def user_content_to_codex(content: Sequence[UserContent]) -> Iterator[UserInput]:
    """Convert pydantic-ai UserContent list to Codex UserInput list."""
    from codexed.models import ImageUserInput, TextUserInput

    for item in content:
        match item:
            case str(text) | TextContent(content=text):
                yield TextUserInput(text=text)
            case ImageUrl(url=url):
                yield ImageUserInput(url=url)
            case BinaryContent(data=data, media_type=media_type, is_image=is_image) if is_image:
                b64 = base64.b64encode(data).decode()
                data_uri = f"data:{media_type};base64,{b64}"
                yield ImageUserInput(url=data_uri)
            case FileUrl() | BinaryContent() | CachePoint() | UploadedFile():
                pass
            case _ as unreachable:
                assert_never(unreachable)


async def _format_tool_result(item: ThreadItem) -> str | list[str | BinaryContent]:
    """Format tool result from a completed ThreadItem.

    Args:
        item: Completed thread item

    Returns:
        Formatted result string, or list of content items for MCP tool results.
    """
    from codexed.models import (
        ThreadItemCommandExecution,
        ThreadItemFileChange,
        ThreadItemMcpToolCall,
    )

    from agentpool.mcp_server.conversions import from_mcp_content

    match item:
        case ThreadItemCommandExecution(aggregated_output=output):
            return f"```\n{output}\n```" or ""
        case ThreadItemFileChange(changes=changes):
            # Format file changes with their diffs
            parts = []
            for change in changes:
                parts.append(f"{change.kind.type.upper()}: {change.path}")
                if change.diff:
                    parts.append(change.diff)
            return "\n".join(parts)
        case ThreadItemMcpToolCall(result=result) if result and result.content:
            return await from_mcp_content(result.content)
        case ThreadItemMcpToolCall(error=error) if error:
            return f"Error: {error.message}"
        case ThreadItemWebSearch():
            return ""
        case _:
            return ""


def _thread_item_to_tool_call_part(item: ThreadItem) -> ToolCallPart | BuiltinToolCallPart | None:  # noqa: PLR0911
    """Convert a ThreadItem to a ToolCallPart or BuiltinToolCallPart.

    Codex built-in tools (bash, file changes, web search, etc.) are converted to
    BuiltinToolCallPart since they're provided by the remote Codex agent.
    MCP tools are converted to ToolCallPart (they may be from local ToolBridge).

    Args:
        item: Thread item from Codex

    Returns:
        ToolCallPart for MCP tools, BuiltinToolCallPart for Codex built-ins, or None
    """
    from codexed.models import (
        ThreadItemCommandExecution,
        ThreadItemFileChange,
        ThreadItemImageView,
        ThreadItemMcpToolCall,
        ThreadItemWebSearch,
    )

    match item:
        case ThreadItemCommandExecution(id=tc_id):
            return BuiltinToolCallPart(
                tool_name="bash",
                args=item.inferred_arguments,
                tool_call_id=tc_id,
            )
        case ThreadItemFileChange(id=tc_id):
            return BuiltinToolCallPart(
                tool_name="file_change",
                args=item.inferred_arguments,
                tool_call_id=tc_id,
            )
        case ThreadItemWebSearch(id=tc_id):
            return BuiltinToolCallPart(
                tool_name="web_search",
                args=item.inferred_arguments,
                tool_call_id=tc_id,
            )
        case ThreadItemImageView(id=tc_id):
            return BuiltinToolCallPart(
                tool_name="image_view",
                args=item.inferred_arguments,
                tool_call_id=tc_id,
            )
        case ThreadItemCollabAgentToolCall(id=tc_id):
            return BuiltinToolCallPart(
                tool_name="subagent",
                args=item.inferred_arguments,
                tool_call_id=tc_id,
            )
        case (
            ThreadItemMcpToolCall(id=id_, tool=tool, arguments=arguments)
            | ThreadItemDynamicToolCall(id=id_, tool=tool, arguments=arguments)
        ):
            # TODO: Distinguish between local (ToolBridge) and remote MCP tools
            # Currently all MCP tools use ToolCallPart, but ideally:
            # - Tools from AgentPool's ToolBridge → ToolCallPart (our tools)
            # - Tools from Codex's own MCP servers → BuiltinToolCallPart (their tools)
            # This requires tracking which tools came from ToolBridge vs Codex config
            return ToolCallPart(tool_name=tool, args=arguments or {}, tool_call_id=id_)
        case (
            ThreadItemAgentMessage()
            | ThreadItemContextCompaction()
            | ThreadItemUserMessage()
            | ThreadItemReasoning()
            | ThreadItemPlan()
            | ThreadItemEnteredReviewMode()
            | ThreadItemExitedReviewMode()
        ):
            return None
        case _ as unreachable:
            assert_never(unreachable)


def _user_input_to_content(inp: UserInput) -> UserContent:
    """Convert Codex UserInput to pydantic-ai UserContent."""
    from codexed.models import (
        ImageUserInput,
        LocalImageUserInput,
        MentionUserInput,
        SkillUserInput,
        TextUserInput,
    )

    match inp:
        case TextUserInput(text=text):
            return text
        case ImageUserInput(url=url):
            return ImageUrl(url=url)
        case LocalImageUserInput(path=path):
            return ImageUrl(url=f"file://{path}")
        case SkillUserInput(name=name):
            return f"[Skill: {name}]"
        case MentionUserInput(name=name):
            return f"@{name}"
        case _ as unreachable:
            assert_never(unreachable)


def _turn_to_chat_messages(turn: Turn) -> list[ChatMessage[list[UserContent]]]:  # noqa: PLR0915
    """Convert one Turn to ChatMessages (user and optionally assistant).

    Each ThreadItem in the turn becomes one "conversational beat" in the assistant
    message's messages list (one ModelResponse per item).

    Args:
        turn: Single Turn from Codex thread

    Returns:
        List of ChatMessages - always includes user message, assistant message
        only if there are assistant responses (handles interrupted/incomplete turns)
    """
    from codexed.models import (
        ThreadItemAgentMessage,
        ThreadItemCollabAgentToolCall,
        ThreadItemCommandExecution,
        ThreadItemEnteredReviewMode,
        ThreadItemExitedReviewMode,
        ThreadItemFileChange,
        ThreadItemImageView,
        ThreadItemMcpToolCall,
        ThreadItemReasoning,
        ThreadItemUserMessage,
        ThreadItemWebSearch,
    )

    user_content: list[UserContent] = []
    assistant_responses: list[ModelRequest | ModelResponse] = []  # One per ThreadItem

    for item in turn.items:
        match item:
            case ThreadItemUserMessage(content=msg_content):
                user_content.extend(_user_input_to_content(i) for i in msg_content)
            case ThreadItemAgentMessage(text=text):
                assistant_responses.append(ModelResponse(parts=[TextPart(content=text)]))
            case ThreadItemReasoning(summary=summary):
                # summary is list[str] - create one ThinkingPart per summary item
                # But we want one ModelResponse per ThreadItem, so combine them
                thinking_parts = [ThinkingPart(content=s) for s in summary]
                assistant_responses.append(ModelResponse(parts=thinking_parts))
            case ThreadItemCommandExecution(id=tc_id, aggregated_output=out):
                output = out or ""
                parts = get_builtin_tool_parts(
                    tool_name="bash",
                    args=item.inferred_arguments,
                    tc_id=tc_id,
                    content=output,
                )
                assistant_responses.append(ModelResponse(parts=parts))

            case ThreadItemFileChange(id=tc_id):
                parts = get_builtin_tool_parts(
                    tool_name="edit",
                    args=item.inferred_arguments,
                    tc_id=tc_id,
                    content="Edit successful.",
                )
                assistant_responses.append(ModelResponse(parts=parts))

            case ThreadItemMcpToolCall(result=mcp_result, arguments=mcp_args, id=tc_id, tool=tool):
                result_text = ""
                if mcp_result and mcp_result.content:
                    texts = [str(b.model_dump().get("text", "")) for b in mcp_result.content]
                    result_text = " ".join(texts)
                args = mcp_args or {}
                parts = get_builtin_tool_parts(
                    tool_name=tool, args=args, tc_id=tc_id, content=result_text
                )
                assistant_responses.append(ModelResponse(parts=parts))

            case ThreadItemDynamicToolCall(
                content_items=items,
                arguments=args,
                id=tc_id,
                tool=tool,
            ):
                result_text = ""
                if items:
                    texts = [str(b.model_dump().get("text", "")) for b in items]
                    result_text = " ".join(texts)
                parts = get_builtin_tool_parts(
                    tool_name=tool, args=args or {}, tc_id=tc_id, content=result_text
                )
                assistant_responses.append(ModelResponse(parts=parts))

            case ThreadItemWebSearch(query=query, id=tc_id):
                parts = get_builtin_tool_parts(
                    tool_name="web_search",
                    args={"query": query},
                    tc_id=tc_id,
                    content="Search completed",
                )
                assistant_responses.append(ModelResponse(parts=parts))

            case ThreadItemImageView(path=path, id=tc_id):
                parts = get_builtin_tool_parts(
                    tool_name="view_image",
                    args={"path": path},
                    tc_id=tc_id,
                    content="Image viewed",
                )
                assistant_responses.append(ModelResponse(parts=parts))

            case ThreadItemEnteredReviewMode(review=review):
                tp = TextPart(content=f"Entered review mode: {review}")
                assistant_responses.append(ModelResponse(parts=[tp]))

            case ThreadItemExitedReviewMode(review=review):
                tp = TextPart(content=f"Exited review mode: {review}")
                assistant_responses.append(ModelResponse(parts=[tp]))

            case ThreadItemCollabAgentToolCall(tool=tool, id=tc_id, agents_states=agents_states):
                # Get first agent state from the dict, if any
                first_state = next(iter(agents_states.values()), None)
                status = first_state.status if first_state else "unknown"
                parts = get_builtin_tool_parts(
                    tool_name="collab_agent",
                    args=item.inferred_arguments,
                    tc_id=tc_id,
                    content=f"Status: {status}",
                )
                assistant_responses.append(ModelResponse(parts=parts))
            case ThreadItemPlan() | ThreadItemContextCompaction():
                pass
            case _ as unreachable:
                assert_never(unreachable)

    # Validate user content exists
    if not user_content:
        return []  # Skip turns with no user content
    result: list[ChatMessage[Any]] = []
    user_msg = ChatMessage[list[UserContent]](
        content=user_content,
        role="user",
        message_id=f"{turn.id}-user",
        messages=[ModelRequest(parts=[UserPromptPart(content=user_content)])],
    )
    result.append(user_msg)

    # Create assistant message only if there are assistant responses
    if assistant_responses:
        assistant_msg = ChatMessage[str](
            content=turn.final_response or "",
            role="assistant",
            message_id=f"{turn.id}-assistant",
            messages=assistant_responses,
        )
        result.append(assistant_msg)

    return result


def turns_to_chat_messages(turns: list[Turn]) -> list[ChatMessage[list[UserContent]]]:
    """Convert Codex turns to ChatMessage list for session loading.

    Each turn produces one or two ChatMessages:
    - User message with content as list[UserContent] (proper types for images etc.)
    - Assistant message (if present) with content as display text, plus messages field
      containing the full ModelMessage structure. Each ThreadItem becomes one
      "conversational beat" (one ModelResponse in the messages list).

    Handles incomplete/interrupted turns that may only have user content.

    Args:
        turns: List of Turn objects from Codex thread

    Returns:
        List of ChatMessages with proper content types and model messages
    """
    return [msg for turn in turns for msg in _turn_to_chat_messages(turn)]
