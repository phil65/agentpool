"""Converters between pydantic-ai/AgentPool and OpenCode message formats."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, assert_never

import anyenv
from pydantic_ai import (
    ModelRequest,
    ModelResponse,
    RequestUsage,
    RetryFeedbackPart,
    RunUsage,
    TextPart as PydanticTextPart,
    ToolCallPart as PydanticToolCallPart,
    ToolReturnPart as PydanticToolReturnPart,
    UserPromptPart,
)

from agentpool import log
from agentpool.messaging import TokenCost
from agentpool.messaging.messages import ChatMessage
from agentpool.sessions.models import SessionData
from agentpool.tools.manager import ToolError
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict, to_user_content_or_path_ref
from agentpool.utils.time_utils import datetime_to_ms, ms_to_datetime
from opencode_sdk.models import (
    AgentPartInput,
    FilePartInput,
    MCPStatus,
    MessagePath,
    MessageTime,
    MessageWithParts,
    ModelRef,
    ResourceSource,
    Session,
    SessionRevert,
    SessionShare,
    SubtaskPartInput,
    TextPart,
    TextPartInput,
    TimeCreated,
    TimeCreatedUpdated,
    TimeStart,
    TimeStartEnd,
    TimeStartEndCompacted,
    TimeStartEndOptional,
    Tokens,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
    ToolStateRunning,
    UserMessage,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from fsspec.asyn import AsyncFileSystem
    from pydantic_ai import FinishReason, ModelMessage, UserContent

    from agentpool.common_types import MCPConnectionStatus, MCPServerStatus, PathReference
    from agentpool.tools.manager import ToolManager
    from opencode_sdk.models import (
        AnyMessageWithParts,
        FinishReason as OCFinishReason,
        MCPConnectionStatus as OpenCodeMCPConnectionStatus,
        PartInput,
    )


logger = log.get_logger(__name__)

# Parameter name mapping from snake_case to camelCase for OpenCode TUI compatibility
_PARAM_NAME_MAP: dict[str, str] = {
    "path": "filePath",
    "file_path": "filePath",
    "old_string": "oldString",
    "new_string": "newString",
    "replace_all": "replaceAll",
    "line_hint": "lineHint",
}


def to_mcp_status(status: MCPServerStatus) -> MCPStatus:
    status_val = to_opencode_mcp_status(status.status)
    return MCPStatus(name=status.name, status=status_val, error=status.error)


def to_opencode_mcp_status(status: MCPConnectionStatus) -> OpenCodeMCPConnectionStatus:
    mapping: dict[MCPConnectionStatus, OpenCodeMCPConnectionStatus] = {
        "connected": "connected",
        "disconnected": "disconnected",
        "error": "error",
        "pending": "disconnected",
        "failed": "error",
        "needs-auth": "disconnected",
        "disabled": "disconnected",
    }
    return mapping[status]


def _convert_params_for_ui(params: dict[str, Any]) -> dict[str, Any]:
    """Convert parameter names from snake_case to camelCase for OpenCode TUI.

    OpenCode TUI expects camelCase parameter names like 'filePath', 'oldString', etc.
    This converts our snake_case parameters to match those expectations.
    """
    return {_PARAM_NAME_MAP.get(k, k): v for k, v in params.items()}


async def _resolve_mcp_resource(source: ResourceSource, tools: ToolManager) -> str | None:
    """Resolve an MCP resource and return its content as text (or None if cant be read)."""
    try:
        resource = await tools.get_resource(source.uri)
    except ToolError:
        logger.warning("MCP resource not found", client_name=source.client_name, uri=source.uri)
        return None
    try:
        contents = await resource.read()
        return "\n".join(contents) if contents else None
    except Exception:
        logger.exception(
            "Failed to read MCP resource", client_name=source.client_name, uri=source.uri
        )
        return None


async def extract_user_prompt_from_parts(
    parts: list[PartInput],
    fs: AsyncFileSystem | None = None,
    tools: ToolManager | None = None,
) -> Sequence[UserContent | PathReference]:
    """Extract user prompt from OpenCode message input parts.

    Converts OpenCode input parts to pydantic-ai UserContent or PathReference format:
    - Text parts become strings
    - File parts with file:// URLs become PathReference (deferred resolution)
    - File parts with ResourceSource are resolved via MCP
    - Other file parts become ImageUrl, DocumentUrl, AudioUrl, VideoUrl, or BinaryContent
    - Agent parts inject instructions to delegate to sub-agents
    - Subtask parts inject instructions for spawning subtasks

    Args:
        parts: List of OpenCode message input parts
        fs: Optional async filesystem for PathReference resolution
        tools: Optional tool manager for resolving MCP resources

    Returns:
        Either a simple string (text-only) or a list of UserContent/PathReference items
    """
    result: list[UserContent | PathReference] = []
    for part in parts:
        match part:
            case TextPartInput(text=text):
                result.append(text)
            case FilePartInput(source=ResourceSource() as resource) if tools is not None:
                content = await _resolve_mcp_resource(resource, tools)
                if content is not None:
                    result.append(content)
            case FilePartInput(mime=mime, url=url, filename=filename):
                file_content = to_user_content_or_path_ref(mime, url, filename, fs=fs)
                result.append(file_content)
            case AgentPartInput(name=agent_name):
                # Agent mention (@agent-name in prompt) - inject instruction to execute task
                # This mirrors OpenCode's server-side behavior: inject a synthetic
                # text instruction telling the LLM to call the task tool
                instruction = (
                    f"Use the above message and context to generate a prompt "
                    f"and call the task tool with agent_or_team='{agent_name}'"
                )
                result.append(instruction)
            case SubtaskPartInput(agent=agent, prompt=subtask_prompt, description=desc):
                # Subtask - explicit task execution with pre-defined prompt
                # Inject instruction to call task with the provided parameters
                instruction = (
                    f"Call the task tool with:\n"
                    f"  agent_or_team: '{agent}'\n"
                    f"  prompt: '{subtask_prompt}'\n"
                    f"  description: '{desc}'"
                )
                result.append(instruction)
            case _ as unreachable:
                assert_never(unreachable)

    return result


# =============================================================================
# ChatMessage <-> OpenCode MessageWithParts Converters
# =============================================================================


def _is_retry_part(part: object) -> bool:
    """Whether a message part represents a retry (tool retry or retry feedback)."""
    match part:
        case RetryFeedbackPart() | PydanticToolReturnPart(outcome="retried"):
            return True
        case _:
            return False


def _retry_message(part: RetryFeedbackPart | PydanticToolReturnPart[Any]) -> str:
    """Get the retry message shown to the model for a retry part."""
    match part:
        case RetryFeedbackPart():
            return part.model_response()
        case PydanticToolReturnPart():
            return part.model_response_str(wrap_if_error=False)


def chat_message_to_opencode(  # noqa: PLR0915
    msg: ChatMessage[Any],
    session_id: str,
    working_dir: str = "",
    agent_name: str = "default",
    model_id: str = "unknown",
    provider_id: str = "agentpool",
) -> AnyMessageWithParts:
    """Convert a ChatMessage to OpenCode MessageWithParts.

    Args:
        msg: The ChatMessage to convert
        session_id: OpenCode session ID
        working_dir: Working directory for path context
        agent_name: Name of the agent
        model_id: Model identifier
        provider_id: Provider identifier

    Returns:
        OpenCode MessageWithParts with appropriate info and parts
    """
    message_id = msg.message_id
    created_ms = datetime_to_ms(msg.timestamp)
    if msg.role == "user":
        user_msg = MessageWithParts.user(
            message_id=message_id,
            session_id=session_id,
            time=TimeCreated(created=created_ms),
            agent_name=agent_name,
            model=ModelRef(provider_id=provider_id, model_id=model_id),
        )
        if msg.content and isinstance(msg.content, str):
            ts_opt = TimeStartEndOptional(start=created_ms)
            user_msg.add_text_part(msg.content, time=ts_opt)
        else:
            for model_msg in msg.messages:
                if not isinstance(model_msg, ModelRequest):
                    continue
                for part in model_msg.parts:
                    if not isinstance(part, UserPromptPart):
                        continue
                    content = part.content
                    if isinstance(content, str):
                        text = content
                    else:
                        text = " ".join(str(c) for c in content if isinstance(c, str))
                    if text:
                        ts_opt = TimeStartEndOptional(start=created_ms)
                        user_msg.add_text_part(text, time=ts_opt)
        return user_msg
    # Assistant message
    completed_ms = created_ms
    if msg.response_time:
        completed_ms = created_ms + int(msg.response_time * 1000)

    tokens = Tokens.from_pydantic_ai(msg.usage)
    message = MessageWithParts.assistant(
        message_id=message_id,
        session_id=session_id,
        parent_id="",  # Would need to track parent user message
        model_id=msg.model_name or model_id,
        provider_id=msg.provider_name or provider_id,
        mode="default",
        agent_name=agent_name,
        path=MessagePath(cwd=working_dir, root=working_dir),
        time=MessageTime(created=created_ms, completed=completed_ms),
        tokens=tokens,
        cost=float(msg.cost_info.total_cost) if msg.cost_info else 0.0,
        finish=to_oc_finish_reason(msg.finish_reason),
    )

    message.add_step_start_part()
    # Process all model messages to extract parts
    tool_calls: dict[str, ToolPart] = {}
    for model_msg in msg.messages:
        for p in model_msg.parts:
            match p:
                case PydanticTextPart(content=content):
                    ts_opt = TimeStartEndOptional(start=created_ms, end=completed_ms)
                    message.add_text_part(content, time=ts_opt)
                case PydanticToolCallPart(tool_name=tool_name, tool_call_id=call_id):
                    tool_input = _convert_params_for_ui(safe_args_as_dict(p))
                    ts = TimeStart(start=created_ms)
                    title = f"Running {tool_name}"
                    running_state = ToolStateRunning(time=ts, input=tool_input, title=title)
                    tool_part = message.add_tool_part(tool_name, call_id, state=running_state)
                    tool_calls[call_id] = tool_part
                case (
                    RetryFeedbackPart(content=retry_content, timestamp=ts)
                    | PydanticToolReturnPart(outcome="retried", content=retry_content, timestamp=ts)
                ):
                    retry_count = sum(
                        1 for m in msg.messages for rp in m.parts if _is_retry_part(rp)
                    )
                    if isinstance(retry_content, list):
                        error_type = "validation_error"
                    elif isinstance(p, PydanticToolReturnPart):
                        error_type = "tool_error"
                    else:
                        error_type = "retry"

                    message.add_retry_part(
                        attempt=retry_count,
                        message=_retry_message(p),
                        created=int(ts.timestamp() * 1000),
                        is_retryable=True,
                        metadata={"error_type": error_type},
                    )
                case PydanticToolReturnPart(
                    tool_call_id=call_id,
                    content=tool_content,
                    tool_name=tool_name,
                    timestamp=tool_ts,
                ):
                    end_ms = datetime_to_ms(tool_ts)
                    match tool_content:
                        case str():
                            output = tool_content
                        case dict():
                            output = anyenv.dump_json(tool_content, indent=True)
                        case None:
                            output = ""
                        case _:
                            output = str(tool_content)
                    if existing := tool_calls.get(call_id):
                        if isinstance(tool_content, dict) and "error" in tool_content:
                            existing.state = ToolStateError(
                                error=str(tool_content.get("error", "Unknown error")),
                                input=existing.state.input,
                                time=TimeStartEnd(start=created_ms, end=end_ms),
                            )
                        else:
                            title = f"Completed {tool_name}"
                            tsc = TimeStartEndCompacted(start=created_ms, end=end_ms)
                            existing.state = ToolStateCompleted(
                                title=title, input=existing.state.input, output=output, time=tsc
                            )
                    else:
                        # Orphan return - create completed tool part
                        state: ToolStateCompleted | ToolStateError
                        if isinstance(tool_content, dict) and "error" in tool_content:
                            err = str(tool_content.get("error", "Unknown error"))
                            ts_end = TimeStartEnd(start=created_ms, end=end_ms)
                            state = ToolStateError(error=err, time=ts_end)
                        else:
                            title = f"Completed {tool_name}"
                            tsc = TimeStartEndCompacted(start=created_ms, end=end_ms)
                            state = ToolStateCompleted(title=title, output=output, time=tsc)
                        message.add_tool_part(tool_name, call_id, state=state)
    cost = float(msg.cost_info.total_cost) if msg.cost_info else 0.0
    message.add_step_finish_part(reason=msg.finish_reason or "stop", cost=cost, tokens=tokens)

    return message


def opencode_to_chat_message(
    msg: AnyMessageWithParts,
    session_id: str | None = None,
) -> ChatMessage[str]:
    """Convert OpenCode MessageWithParts to ChatMessage.

    Args:
        msg: OpenCode message with parts
        session_id: Optional conversation ID override

    Returns:
        ChatMessage with pydantic-ai model messages
    """
    info = msg.info
    message_id = info.id
    session_id = info.session_id
    created_ms = info.time.created
    timestamp = ms_to_datetime(created_ms)
    model_messages: list[ModelMessage] = []
    # Determine role and extract timing
    if isinstance(info, UserMessage):
        model_name = info.model.model_id
        provider_name = info.model.provider_id
        usage = RequestUsage()
        run_usage = RunUsage()
        finish_reason = None
        total_cost = None
        text_content = [part.text for part in msg.parts if isinstance(part, TextPart)]
        content = "\n".join(text_content) if text_content else ""
        model_messages.append(ModelRequest(parts=[UserPromptPart(content=content)]))
    else:
        model_name = info.model_id
        provider_name = info.provider_id
        usage = info.tokens.to_request_usage()
        run_usage = info.tokens.to_run_usage()
        total_cost = info.cost
        finish_reason = info.finish
        # Assistant message - collect response parts and tool interactions
        response_parts: list[Any] = []
        tool_returns: list[PydanticToolReturnPart] = []
        for part in msg.parts:
            match part:
                case TextPart(text=text, id=part_id):
                    response_parts.append(PydanticTextPart(content=text, id=part_id))
                case ToolPart(tool=tool_name, call_id=call_id, state=state):
                    response_parts.append(
                        PydanticToolCallPart(
                            tool_name=tool_name,
                            tool_call_id=call_id,
                            args=state.input,
                        )
                    )
                    match state:
                        case ToolStateCompleted(output=output):
                            tool_returns.append(
                                PydanticToolReturnPart(
                                    tool_name=tool_name,
                                    tool_call_id=call_id,
                                    content=output,
                                )
                            )
                        case ToolStateError(error=error):
                            tool_returns.append(
                                PydanticToolReturnPart(
                                    tool_name=tool_name,
                                    tool_call_id=call_id,
                                    content={"error": error},
                                )
                            )

        if response_parts:
            model_messages.append(
                ModelResponse(
                    parts=response_parts,
                    usage=usage,
                    model_name=model_name,
                    timestamp=timestamp,
                )
            )

        # Add tool returns as a follow-up request if any
        if tool_returns:
            model_messages.append(ModelRequest(parts=tool_returns, instructions=None))
    # Extract content from text parts after the last tool call,
    # matching pydantic-ai's behavior of only using the final response's text.
    last_tool_idx = -1
    for i, part in enumerate(msg.parts):
        if isinstance(part, ToolPart):
            last_tool_idx = i
    content = "".join(
        p.text for i, p in enumerate(msg.parts) if isinstance(p, TextPart) and i > last_tool_idx
    )
    return ChatMessage(
        content=content,
        role=info.role,
        message_id=message_id,
        session_id=session_id or session_id,
        timestamp=timestamp,
        cost_info=TokenCost(total_cost=Decimal(total_cost)) if total_cost else None,
        messages=model_messages,
        usage=run_usage,
        model_name=model_name,
        provider_name=provider_name,
        finish_reason=to_native_finish_reason(finish_reason),
    )


def to_native_finish_reason(reason: str | OCFinishReason | None) -> FinishReason:
    if reason is None:
        return "stop"
    mapping: dict[OCFinishReason | str, FinishReason] = {
        "stop": "stop",
        "length": "length",
        "tool-calls": "tool_call",
        "content-filter": "content_filter",
        "error": "error",
        "unknown": "stop",
    }
    return mapping.get(reason, "stop")


def to_oc_finish_reason(reason: FinishReason | None) -> OCFinishReason | None:
    if reason is None:
        return None
    mapping: dict[FinishReason, OCFinishReason] = {
        "stop": "stop",
        "length": "length",
        "tool_call": "tool-calls",
        "content_filter": "content-filter",
        "error": "error",
    }
    return mapping.get(reason, "stop")


# =============================================================================
# Session Converters
# =============================================================================


def session_data_to_opencode(data: SessionData) -> Session:
    """Convert SessionData to OpenCode Session model.

    Args:
        data: SessionData to convert (title comes from data.title property)
    """
    # Convert datetime to milliseconds timestamp
    created_ms = datetime_to_ms(data.created_at)
    updated_ms = datetime_to_ms(data.last_active)
    # Extract revert/share from metadata if present
    return Session(
        id=data.session_id,
        project_id=data.project_id or "default",
        directory=data.cwd or "",
        title=data.title or "New Session",
        version=data.version,
        time=TimeCreatedUpdated(created=created_ms, updated=updated_ms),
        parent_id=data.parent_id,
        revert=SessionRevert(**data.metadata["revert"]) if "revert" in data.metadata else None,
        share=SessionShare(**data.metadata["share"]) if "share" in data.metadata else None,
    )


def opencode_to_session_data(
    session: Session,
    *,
    agent_name: str = "default",
    pool_id: str | None = None,
) -> SessionData:
    """Convert OpenCode Session to SessionData for persistence."""
    # Store revert/share in metadata
    metadata: dict[str, Any] = {}
    if session.revert:
        metadata["revert"] = session.revert.model_dump()
    if session.share:
        metadata["share"] = session.share.model_dump()
    return SessionData(
        session_id=session.id,
        agent_name=agent_name,
        pool_id=pool_id,
        project_id=session.project_id,
        parent_id=session.parent_id,
        version=session.version,
        cwd=session.directory,
        created_at=ms_to_datetime(session.time.created),
        last_active=ms_to_datetime(session.time.updated),
        metadata=metadata,
    )
