"""Converters between pydantic-ai/AgentPool and OpenCode message formats."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, assert_never, cast

import anyenv
from pydantic_ai import (
    ModelRequest,
    ModelResponse,
    RequestUsage,
    RetryPromptPart,
    TextPart as PydanticTextPart,
    ThinkingPart as PydanticThinkingPart,
    ToolCallPart as PydanticToolCallPart,
    ToolReturnPart as PydanticToolReturnPart,
    UserPromptPart,
)

from wolfharness import log
from wolfharness.messaging.messages import ChatMessage
from wolfharness.sessions.models import SessionData
from wolfharness.utils import identifiers as identifier
from wolfharness.utils.pydantic_ai_helpers import safe_args_as_dict, to_user_content_or_path_ref
from wolfharness.utils.time_utils import datetime_to_ms, ms_to_datetime
from wolfharness_server.opencode_server.models import (
    AgentPartInput,
    FilePart,
    FilePartInput,
    MCPStatus,
    MessagePath,
    MessageTime,
    MessageWithParts,
    ReasoningPart,
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
    ToolStatePending,
    ToolStateRunning,
    UserMessage,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from fsspec.asyn import AsyncFileSystem
    from pydantic_ai import UserContent

    from wolfharness.agents.base_agent import BaseAgent
    from wolfharness.common_types import MCPConnectionStatus, MCPServerStatus, PathReference
    from wolfharness.models.model_configs import AnyModelConfig
    from wolfharness_server.opencode_server.models import ToolState
    from wolfharness_server.opencode_server.models.mcp import (
        MCPConnectionStatus as OpenCodeMCPConnectionStatus,
    )
    from wolfharness_server.opencode_server.models.message import PartInput
    from wolfharness_server.opencode_server.models.parts import ResourceSource


logger = log.get_logger(__name__)

# Parameter name mapping from snake_case to camelCase for OpenCode TUI compatibility
_PARAM_NAME_MAP: dict[str, str] = {
    "path": "filePath",
    "file_path": "filePath",
    "uri": "filePath",
    "old_string": "oldString",
    "new_string": "newString",
    "replace_all": "replaceAll",
    "line_hint": "lineHint",
}


def to_mcp_status(status: MCPServerStatus) -> MCPStatus:
    return MCPStatus(
        name=status.name,
        display_name=status.display_name or status.name,
        status=to_opencode_mcp_status(status.status),
        tools=status.tools,
        error=status.error,
    )


def to_opencode_mcp_status(status: MCPConnectionStatus) -> OpenCodeMCPConnectionStatus:
    # Note: 'disabled' is an internal status (server configured but
    # enabled=False). The OpenCode API has no 'disabled' state, so it
    # maps to 'disconnected'.
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


def _get_input_from_state(state: ToolState, *, convert_params: bool = False) -> dict[str, Any]:
    """Extract input from any tool state type.

    Args:
        state: Tool state to extract input from
        convert_params: If True, convert param names to camelCase for UI display
    """
    return _convert_params_for_ui(state.input) if convert_params else state.input


async def _resolve_resource(
    source: ResourceSource, agent: BaseAgent[Any, Any], session_id: str
) -> list[UserContent] | None:
    """Resolve a resource and return its content as a list of UserContent items.

    Uses the agent's ``ExtensionRegistry`` (via ``host_context``) with a
    session-scoped ``Scope`` to find ``ResourceAccess`` and
    ``SkillResource`` providers. The agent name is required so AGENT-scoped
    providers remain visible while SESSION-scoped providers are merged in.

    Returns None if the resource is not found.
    """
    from wolfharness.capabilities.extension_registry import Scope, ScopeLevel
    from wolfharness.capabilities.resource_resolver import resolve_resource_content

    scope = Scope(
        level=ScopeLevel.SESSION,
        agent_name=agent.name,
        session_id=session_id,
    )
    host_ctx = agent.host_context
    if host_ctx is None:
        raise RuntimeError(f"Agent host_context is None, cannot resolve resource {source.uri!r}")
    registry = host_ctx.extension_registry
    if registry is None:
        raise RuntimeError(
            f"Agent extension_registry is None, cannot resolve resource {source.uri!r}"
        )
    from wolfharness.capabilities.resource_protocols import NamedResourceAccess, ResourceAccess

    resource_caps: list[ResourceAccess] = [
        provider
        for provider in registry.get_resource_access(scope)
        if isinstance(provider, NamedResourceAccess) and provider.client_name == source.client_name
    ]
    skill_caps = registry.get_skill_resources(scope)

    if source.uri.startswith("skill://"):
        resource_caps = []

    content = await resolve_resource_content(source.uri, resource_caps, skill_caps)
    if content is None:
        logger.warning("Resource not found", client_name=source.client_name, uri=source.uri)
    return content


async def extract_user_prompt_from_parts(
    parts: list[PartInput],
    session_id: str,
    fs: AsyncFileSystem | None = None,
    agent: BaseAgent[Any, Any] | None = None,
    normalizer: Any | None = None,
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
        agent: Optional agent for resolving MCP resources
        session_id: Session ID for scoped resource resolution via
            ExtensionRegistry.
        normalizer: Optional ``ImageNormalizer`` for normalizing oversized
            ``image/*`` data-URI attachments (RFC-0059). When provided,
            oversized image data URIs are resized/re-encoded before being
            converted to pydantic-ai content.

    Returns:
        Either a simple string (text-only) or a list of UserContent/PathReference items
    """
    from wolfharness_server.opencode_server.models.parts import ResourceSource

    result: list[UserContent | PathReference] = []
    for part in parts:
        match part:
            case TextPartInput(text=text):
                result.append(text)
            case FilePartInput(source=ResourceSource() as resource) if agent is not None:
                content = await _resolve_resource(resource, agent, session_id=session_id)
                if content is not None:
                    result.extend(content)
            case FilePartInput(mime=mime, url=url, filename=filename):
                if normalizer is not None and mime.startswith("image/"):
                    url, mime = normalizer.normalize(url, mime)
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
            case SubtaskPartInput(agent=subtask_agent, prompt=subtask_prompt, description=desc):
                # Subtask - explicit task execution with pre-defined prompt
                # Inject instruction to call task with the provided parameters
                instruction = (
                    f"Call the task tool with:\n"
                    f"  agent_or_team: '{subtask_agent}'\n"
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


def chat_message_to_opencode(  # noqa: PLR0915
    msg: ChatMessage[Any],
    session_id: str,
    working_dir: str = "",
    agent_name: str = "default",
    model_id: str = "unknown",
    provider_id: str = "wolfharness",
    model_variants: dict[str, AnyModelConfig] | None = None,
) -> MessageWithParts:
    """Convert a ChatMessage to OpenCode MessageWithParts.

    Args:
        msg: The ChatMessage to convert
        session_id: OpenCode session ID
        working_dir: Working directory for path context
        agent_name: Name of the agent
        model_id: Model identifier (fallback when model_variants is None)
        provider_id: Provider identifier (fallback when model_variants is None)
        model_variants: Optional dict of variant name → config from manifest.
            When provided and non-empty, resolves model_id/provider_id from
            the message's raw model_name/provider_name via variant lookup.

    Returns:
        OpenCode MessageWithParts with appropriate info and parts
    """
    message_id = msg.message_id
    created_ms = datetime_to_ms(msg.timestamp)
    if msg.role == "user":
        result = MessageWithParts.user(
            message_id=message_id,
            session_id=session_id,
            time=TimeCreated(created=created_ms),
            agent_name=msg.name or agent_name,
        )
        if msg.content and isinstance(msg.content, str):
            ts_opt = TimeStartEndOptional(start=created_ms)
            result.add_text_part(msg.content, time=ts_opt)
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
                        from wolfharness.agents.native_agent.helpers import _summarize_content_block

                        text = " ".join(_summarize_content_block(c) for c in content)
                    if text:
                        ts_opt = TimeStartEndOptional(start=created_ms)
                        result.add_text_part(text, time=ts_opt)
    else:
        # Assistant message
        completed_ms = created_ms
        if msg.response_time:
            completed_ms = created_ms + int(msg.response_time * 1000)

        tokens = Tokens.from_pydantic_ai(msg.usage)
        if model_variants:
            from wolfharness_server.shared.model_utils import resolve_model_info_from_response

            resolved_id, resolved_provider = resolve_model_info_from_response(
                msg.model_name, msg.provider_name, model_variants
            )
            model_id = resolved_id
            provider_id = resolved_provider
        else:
            model_id = msg.model_name or model_id
            provider_id = msg.provider_name or provider_id
        result = MessageWithParts.assistant(
            message_id=message_id,
            session_id=session_id,
            parent_id="",  # Would need to track parent user message
            model_id=model_id,
            provider_id=provider_id,
            agent_name=msg.name or agent_name,
            path=MessagePath(cwd=working_dir, root=working_dir),
            time=MessageTime(created=created_ms, completed=completed_ms),
            tokens=tokens,
            cost=float(msg.cost_info.total_cost) if msg.cost_info else 0.0,
            finish=msg.finish_reason,
            mode=msg.name or agent_name,
        )

        result.add_step_start_part()
        # Process all model messages to extract parts
        tool_calls: dict[str, ToolPart] = {}
        for model_msg in msg.messages:
            # Handle case where message might be a dict (loaded from storage)
            if isinstance(model_msg, dict):
                # Try to extract text content from dict representation
                model_dict = cast(dict[str, Any], model_msg)
                parts = model_dict.get("parts") or []
                for part_dict in parts:
                    if isinstance(part_dict, dict) and part_dict.get("part_kind") == "text":
                        content = part_dict.get("content") or ""
                        if content:
                            ts_opt = TimeStartEndOptional(start=created_ms, end=completed_ms)
                            result.add_text_part(content, time=ts_opt)
                    elif isinstance(part_dict, dict) and part_dict.get("part_kind") == "thinking":
                        content = part_dict.get("content") or ""
                        if content:
                            reasoning_part = ReasoningPart(
                                id=identifier.ascending("part"),
                                message_id=message_id,
                                session_id=session_id,
                                text=content,
                                time=TimeStartEndOptional(start=created_ms, end=completed_ms),
                            )
                            result.parts.append(reasoning_part)
                continue
            for p in model_msg.parts:
                match p:
                    case PydanticThinkingPart(content=content):
                        reasoning_part = ReasoningPart(
                            id=identifier.ascending("part"),
                            message_id=message_id,
                            session_id=session_id,
                            text=content,
                            time=TimeStartEndOptional(start=created_ms, end=completed_ms),
                        )
                        result.parts.append(reasoning_part)
                    case PydanticTextPart(content=content):
                        ts_opt = TimeStartEndOptional(start=created_ms, end=completed_ms)
                        result.add_text_part(content, time=ts_opt)
                    case PydanticToolCallPart(tool_name=tool_name, tool_call_id=call_id):
                        tool_input = _convert_params_for_ui(safe_args_as_dict(p))
                        ts = TimeStart(start=created_ms)
                        title = "Running"
                        running_state = ToolStateRunning(time=ts, input=tool_input, title=title)
                        tool_part = result.add_tool_part(tool_name, call_id, state=running_state)
                        tool_calls[call_id] = tool_part
                    case RetryPromptPart(content=retry_content, tool_name=tool_name, timestamp=ts):
                        retry_count = sum(
                            1
                            for m in msg.messages
                            if isinstance(m, ModelRequest)
                            for p in m.parts
                            if isinstance(p, RetryPromptPart)
                        )
                        error_message = p.model_response()
                        is_retryable = True
                        if isinstance(retry_content, list):
                            error_type = "validation_error"
                        elif tool_name:
                            error_type = "tool_error"
                        else:
                            error_type = "retry"

                        result.add_retry_part(
                            attempt=retry_count,
                            message=error_message,
                            created=int(ts.timestamp() * 1000),
                            is_retryable=is_retryable,
                            metadata={"error_type": error_type} if error_type else None,
                        )
                    case PydanticToolReturnPart(
                        tool_call_id=call_id,
                        content=tool_content,
                        tool_name=tool_name,
                        timestamp=tool_ts,
                    ):
                        end_ms = datetime_to_ms(tool_ts)
                        if isinstance(tool_content, str):
                            output = tool_content
                        elif isinstance(tool_content, dict):
                            output = anyenv.dump_json(tool_content, indent=True)
                        else:
                            output = str(tool_content) if tool_content is not None else ""
                        if existing := tool_calls.get(call_id):
                            existing_input = _get_input_from_state(existing.state)
                            if isinstance(tool_content, dict) and "error" in tool_content:
                                existing.state = ToolStateError(
                                    error=str(tool_content.get("error", "Unknown error")),
                                    input=existing_input,
                                    time=TimeStartEnd(start=created_ms, end=end_ms),
                                )
                            else:
                                title = "Completed"
                                tsc = TimeStartEndCompacted(start=created_ms, end=end_ms)
                                # Extract metadata from tool result if present
                                # (e.g., subagent sessionId)
                                metadata = (
                                    tool_content.get("metadata", {})
                                    if isinstance(tool_content, dict)
                                    else {}
                                )
                                existing.state = ToolStateCompleted(
                                    title=title,
                                    input=existing_input,
                                    output=output,
                                    time=tsc,
                                    metadata=metadata,
                                )
                        else:
                            # Orphan return - create completed tool part
                            state: ToolStateCompleted | ToolStateError
                            if isinstance(tool_content, dict) and "error" in tool_content:
                                err = str(tool_content.get("error", "Unknown error"))
                                ts_end = TimeStartEnd(start=created_ms, end=end_ms)
                                state = ToolStateError(error=err, time=ts_end)
                            else:
                                title = "Completed"
                                tsc = TimeStartEndCompacted(start=created_ms, end=end_ms)
                                # Extract metadata for orphan returns too
                                metadata = (
                                    tool_content.get("metadata", {})
                                    if isinstance(tool_content, dict)
                                    else {}
                                )
                                state = ToolStateCompleted(
                                    title=title, output=output, time=tsc, metadata=metadata
                                )
                            result.add_tool_part(tool_name, call_id, state=state)
        cost = float(msg.cost_info.total_cost) if msg.cost_info else 0.0
        result.add_step_finish_part(reason=msg.finish_reason or "stop", cost=cost, tokens=tokens)

    return result


def opencode_to_chat_message(  # noqa: PLR0915
    msg: MessageWithParts,
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
    # Determine role and extract timing
    if isinstance(info, UserMessage):
        role = "user"
        created_ms = info.time.created
        model_name = info.model.model_id if info.model else None
        provider_name = info.model.provider_id if info.model else None
        usage = RequestUsage()
        finish_reason = None
    else:
        role = "assistant"
        created_ms = info.time.created
        model_name = info.model_id
        provider_name = info.provider_id
        usage = RequestUsage(
            input_tokens=info.tokens.input,
            output_tokens=info.tokens.output,
            cache_read_tokens=info.tokens.cache.read,
            cache_write_tokens=info.tokens.cache.write,
        )
        finish_reason = info.finish

    timestamp = ms_to_datetime(created_ms)
    # Build model messages from parts
    model_messages: list[ModelRequest | ModelResponse] = []
    if role == "user":
        # Collect all parts (text and files/images) into multimodal content list
        from pydantic_ai import BinaryContent, ImageUrl

        content_items: list[str | BinaryContent | ImageUrl] = []
        for part in msg.parts:
            if isinstance(part, TextPart):
                content_items.append(part.text)
            elif isinstance(part, FilePart):
                # Convert file part to appropriate content type
                if part.mime.startswith("image/") and part.url.startswith("data:"):
                    # Data URI image - extract base64 and create BinaryContent
                    # This is the most compatible format for multimodal models
                    content_items.append(BinaryContent.from_data_uri(part.url))
                elif part.mime.startswith("image/"):
                    # Regular image URL (http/https)
                    content_items.append(ImageUrl(url=part.url, media_type=part.mime))
                else:
                    # Other file types - treat as text reference for now
                    content_items.append(f"[File: {part.filename or 'attachment'}]")

        # Create single user prompt with all content items as a list
        # This is the correct format for multimodal prompts in pydantic-ai
        if content_items:
            model_messages.append(ModelRequest(parts=[UserPromptPart(content=content_items)]))
        else:
            model_messages.append(ModelRequest(parts=[UserPromptPart(content="")]))
    else:
        # Assistant message - collect response parts and tool interactions
        response_parts: list[Any] = []
        tool_returns: list[PydanticToolReturnPart] = []
        for part in msg.parts:
            match part:
                case ReasoningPart(text=text, id=part_id):
                    response_parts.append(PydanticThinkingPart(content=text, id=part_id))
                case TextPart(text=text, id=part_id):
                    response_parts.append(PydanticTextPart(content=text, id=part_id))
                case ToolPart(tool=tool_name, call_id=call_id, state=state):
                    response_parts.append(
                        PydanticToolCallPart(
                            tool_name=tool_name,
                            tool_call_id=call_id,
                            args=_get_input_from_state(state),
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
                        case ToolStatePending() | ToolStateRunning():
                            tool_returns.append(
                                PydanticToolReturnPart(
                                    tool_name=tool_name,
                                    tool_call_id=call_id,
                                    content="Tool call was aborted before completion",
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
    # Extract content for the ChatMessage
    content = next((p.text for p in msg.parts if isinstance(p, TextPart)), "")
    return ChatMessage(
        content=content,
        role=role,  # type: ignore[arg-type]
        message_id=message_id,
        session_id=session_id or session_id,
        timestamp=timestamp,
        messages=model_messages,
        usage=usage,
        model_name=model_name,
        provider_name=provider_name,
        finish_reason=finish_reason,  # type: ignore[arg-type]
    )


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
    # Override created_ms with the timestamp embedded in the session ID
    # when available.  Old sessions persisted before the created_at_ns
    # sync fix have stored created_at from get_now() (a separate wall-
    # clock call), which can differ from the session ID's timestamp by
    # milliseconds — enough to cause sort mismatches in the TUI.
    from wolfharness.utils.identifiers import extract_timestamp_ms

    id_ts = extract_timestamp_ms(data.session_id)
    if id_ts is not None:
        created_ms = id_ts
    # Extract revert/share from metadata if present
    revert = None
    share = None
    if "revert" in data.metadata:
        revert = SessionRevert(**data.metadata["revert"])
    if "share" in data.metadata:
        share = SessionShare(**data.metadata["share"])

    # Recompute project_id from cwd when stored value is missing or "default".
    # Older sessions were persisted with project_id="default" which doesn't
    # match the projectID computed from the cwd.
    project_id = data.project_id
    if not project_id or project_id == "default":
        if data.cwd:
            from wolfharness_storage.opencode_provider import helpers

            project_id = helpers.compute_project_id(data.cwd)
        else:
            project_id = "default"

    return Session(
        id=data.session_id,
        project_id=project_id,
        directory=data.cwd or "",
        title=data.title or "New Session",
        version=data.version,
        time=TimeCreatedUpdated(created=created_ms, updated=updated_ms),
        parent_id=data.parent_id,
        revert=revert,
        share=share,
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
    if session.title:
        metadata["title"] = session.title
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
