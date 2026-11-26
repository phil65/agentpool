"""Convert between Google Antigravity SDK and AgentPool types."""

from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, assert_never
from urllib.parse import urlparse
from urllib.request import url2pathname

from mcp.types import ElicitRequestFormParams, ElicitResult
from pydantic_ai import (
    AudioUrl,
    BinaryContent,
    CachePoint,
    DocumentUrl,
    ImageUrl,
    ModelRequest,
    ModelResponse,
    RequestUsage,
    RunUsage,
    TextContent,
    TextPart,
    ThinkingPart,
    UploadedFile,
    UserPromptPart,
    VideoUrl,
)

from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.utils.pydantic_ai_helpers import get_builtin_tool_parts


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from google.antigravity.types import (
        AskQuestionEntry,
        Content,
        ContentPrimitive,
        McpServerConfig,
        QuestionResponse,
        Step,
        StopReason,
        UsageMetadata,
    )
    from mcp.types import ErrorData
    from pydantic_ai import FinishReason, UserContent

    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)

_MCP_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def sanitize_mcp_server_name(name: str) -> str:
    """Normalize an MCP server name to match Antigravity's ``^[a-zA-Z0-9_-]+$`` pattern."""
    sanitized = _MCP_NAME_RE.sub("_", name).strip("_")
    return sanitized or "mcp_server"


def mcp_config_to_antigravity(config: MCPServerConfig) -> McpServerConfig | None:
    """Convert an AgentPool ``MCPServerConfig`` to an Antigravity SDK MCP server config.

    Returns ``None`` if the server is disabled.
    """
    from google.antigravity.types import McpStdioServer, McpStreamableHttpServer

    from agentpool_config.mcp_server import (
        SSEMCPServerConfig,
        StdioMCPServerConfig,
        StreamableHTTPMCPServerConfig,
    )

    if not config.enabled:
        return None

    server_name = sanitize_mcp_server_name(config.name or f"server_{id(config)}")
    match config:
        case StdioMCPServerConfig(command=command, args=args, env=env):
            return McpStdioServer(
                name=server_name,
                command=command,
                args=list(args),
                env=dict(env) if env else None,
                timeout_seconds=int(config.timeout) if config.timeout else None,
            )
        case StreamableHTTPMCPServerConfig(url=url, headers=headers):
            return McpStreamableHttpServer(
                name=server_name,
                url=str(url),
                headers=dict(headers) if headers else None,
                timeout=float(config.timeout) if config.timeout else 30.0,
            )
        case SSEMCPServerConfig(url=url, headers=headers):
            return McpStreamableHttpServer(
                name=server_name,
                url=str(url),
                headers=dict(headers) if headers else None,
                timeout=float(config.timeout) if config.timeout else 30.0,
            )
        case _ as unreachable:
            assert_never(unreachable)


def _file_url_to_path(url: str) -> Path | None:
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    return Path(url2pathname(parsed.path))


def user_content_to_antigravity(prompts: Sequence[UserContent]) -> Content:
    """Convert pydantic-ai ``UserContent`` items to Antigravity SDK ``Content``."""
    from google.antigravity.types import from_bytes, from_file

    parts: list[ContentPrimitive] = []
    for item in prompts:
        match item:
            case str(text) | TextContent(content=text):
                if text:
                    parts.append(text)
            case BinaryContent(data=data, media_type=media_type):
                try:
                    parts.append(from_bytes(data=data, mime_type=media_type))
                except ValueError:
                    logger.warning(
                        "Unsupported MIME type for Antigravity media attachment; skipping",
                        media_type=media_type,
                    )
                    parts.append(f"[Attached binary content: {media_type}, {len(data)} bytes]")
            case ImageUrl(url=url) | DocumentUrl(url=url) | AudioUrl(url=url) | VideoUrl(url=url):
                if (local_path := _file_url_to_path(url)) is not None and local_path.is_file():
                    try:
                        parts.append(from_file(local_path))
                    except OSError, ValueError:
                        logger.warning("Failed to load file URL attachment", url=url)
                        parts.append(url)
                else:
                    parts.append(url)
            case CachePoint() | UploadedFile():
                pass
            case _ as unreachable:
                assert_never(unreachable)

    if not parts:
        return " "
    if len(parts) == 1:
        return parts[0]
    return parts


def to_run_usage(usage: UsageMetadata | None) -> RunUsage:
    """Convert Antigravity ``UsageMetadata`` to pydantic-ai ``RunUsage``."""
    if usage is None:
        return RunUsage()
    candidates = usage.candidates_token_count or 0
    thoughts = usage.thoughts_token_count or 0
    details = {"thoughts_tokens": thoughts} if thoughts else {}
    return RunUsage(
        input_tokens=usage.prompt_token_count or 0,
        output_tokens=candidates + thoughts,
        cache_read_tokens=usage.cached_content_token_count or 0,
        details=details,
    )


def to_request_usage(usage: UsageMetadata) -> RequestUsage:
    """Convert Antigravity ``UsageMetadata`` (of one model invocation) to ``RequestUsage``."""
    candidates = usage.candidates_token_count or 0
    thoughts = usage.thoughts_token_count or 0
    return RequestUsage(
        input_tokens=usage.prompt_token_count or 0,
        output_tokens=candidates + thoughts,
        cache_read_tokens=usage.cached_content_token_count or 0,
        details={"thoughts_tokens": thoughts} if thoughts else {},
    )


def to_finish_reason(stop_reason: StopReason | None, *, failed: bool = False) -> FinishReason:
    """Convert Antigravity ``StopReason`` to pydantic-ai ``FinishReason``."""
    from google.antigravity.types import StopReason as SDKStopReason

    if failed:
        return "error"
    if stop_reason is None:
        return "stop"
    match stop_reason:
        case SDKStopReason.UNSPECIFIED:
            return "stop"
        case (
            SDKStopReason.MAX_INPUT_TOKENS_EXCEEDED
            | SDKStopReason.MAX_OUTPUT_TOKENS_EXCEEDED
            | SDKStopReason.MAX_TOTAL_TOKENS_EXCEEDED
        ):
            return "length"
        case (
            SDKStopReason.MAX_MODEL_CALLS_EXCEEDED
            | SDKStopReason.MAX_TOOL_CALLS_EXCEEDED
            | SDKStopReason.QUOTA_EXHAUSTED
        ):
            return "error"
        case _ as unreachable:
            assert_never(unreachable)


def question_to_elicitation(entry: AskQuestionEntry) -> ElicitRequestFormParams:
    """Convert an Antigravity ``AskQuestionEntry`` into an MCP form elicitation request."""
    prop: dict[str, Any] = {"title": entry.question}
    labels = [option.text for option in entry.options]
    if labels and entry.is_multi_select:
        prop |= {"type": "array", "items": {"type": "string", "enum": labels}}
    elif labels:
        prop |= {"type": "string", "enum": labels}
    else:
        prop["type"] = "string"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"answer": prop},
        "required": ["answer"],
    }
    return ElicitRequestFormParams(message=entry.question, requested_schema=schema)


def elicitation_to_question_response(
    entry: AskQuestionEntry,
    result: ElicitResult | ErrorData,
) -> QuestionResponse:
    """Convert an AgentPool ``InputProvider.get_elicitation`` result to ``QuestionResponse``."""
    from google.antigravity.types import QuestionResponse as SDKQuestionResponse

    if not isinstance(result, ElicitResult) or result.action != "accept" or not result.content:
        return SDKQuestionResponse(skipped=True)

    by_text = {option.text: option.id for option in entry.options}
    by_id = {option.id: option.id for option in entry.options}
    raw = result.content.get("answer")
    match raw:
        case list() as chosen:
            selected_ids: list[str] = []
            freeform_parts: list[str] = []
            for item in chosen:
                text = str(item)
                if (opt_id := by_text.get(text) or by_id.get(text)) is not None:
                    selected_ids.append(opt_id)
                else:
                    freeform_parts.append(text)
            return SDKQuestionResponse(
                selected_option_ids=selected_ids or None,
                freeform_response=", ".join(freeform_parts),
                skipped=not selected_ids and not freeform_parts,
            )
        case str() as text if text:
            if (opt_id := by_text.get(text) or by_id.get(text)) is not None:
                return SDKQuestionResponse(selected_option_ids=[opt_id])
            return SDKQuestionResponse(freeform_response=text)
        case None:
            return SDKQuestionResponse(skipped=True)
        case other:
            return SDKQuestionResponse(freeform_response=str(other))


def steps_to_chat_messages(
    steps: Sequence[Step],
    *,
    session_id: str,
    agent_name: str,
    model_name: str | None = None,
) -> list[ChatMessage[Any]]:
    """Convert Antigravity ``Step`` history into AgentPool ``ChatMessage`` entries.

    Deduplicates repeated updates for the same step ID (keeping the latest snapshot)
    and ignores nested subagent steps (``depth > 0``).
    """
    from google.antigravity.types import StepSource, StepStatus, StepType

    # Deduplicate by step id while preserving order of first appearance.
    latest_by_id: dict[str, Step] = {}
    ordered_ids: list[str] = []
    for step in steps:
        if step.depth > 0 or step.parent_trajectory_id:
            continue
        key = step.id or f"idx:{step.step_index}:{step.type.value}"
        if key not in latest_by_id:
            ordered_ids.append(key)
        latest_by_id[key] = step

    messages: list[ChatMessage[Any]] = []
    pending_assistant_responses: list[ModelRequest | ModelResponse] = []
    pending_assistant_texts: list[str] = []

    def _flush_assistant() -> None:
        if not pending_assistant_responses and not pending_assistant_texts:
            return
        messages.append(
            ChatMessage[str](
                content="\n\n".join(pending_assistant_texts),
                role="assistant",
                name=agent_name,
                session_id=session_id,
                model_name=model_name,
                messages=list(pending_assistant_responses),
            )
        )
        pending_assistant_responses.clear()
        pending_assistant_texts.clear()

    for key in ordered_ids:
        step = latest_by_id[key]
        if step.source == StepSource.USER:
            _flush_assistant()
            text = step.content or ""
            request = ModelRequest(parts=[UserPromptPart(content=text)])
            messages.append(
                ChatMessage[str](
                    content=text,
                    role="user",
                    session_id=session_id,
                    messages=[request],
                )
            )
            continue

        if step.type == StepType.TOOL_CALL and step.tool_calls:
            for call in step.tool_calls:
                tc_id = call.id or step.id or key
                result_text = step.error if step.status == StepStatus.ERROR else ""
                parts = get_builtin_tool_parts(
                    tool_name=str(call.name),
                    args=call.args,
                    tc_id=tc_id,
                    content=result_text,
                )
                pending_assistant_responses.append(
                    ModelResponse(parts=parts, model_name=model_name)
                )
            continue

        if step.source == StepSource.MODEL:
            response_parts: list[TextPart | ThinkingPart] = []
            if step.thinking:
                response_parts.append(ThinkingPart(content=step.thinking))
            if step.content:
                response_parts.append(TextPart(content=step.content))
                pending_assistant_texts.append(step.content)
            if response_parts:
                pending_assistant_responses.append(
                    ModelResponse(parts=response_parts, model_name=model_name)
                )

    _flush_assistant()
    return messages


MANAGED_SDK_OPTIONS = frozenset({
    "system_instructions",
    "capabilities",
    "tools",
    "policies",
    "hooks",
    "mcp_servers",
    "workspaces",
    "conversation_id",
    "session_continuation_mode",
    "save_dir",
    "app_data_dir",
    "response_schema",
    "skills_paths",
    "compaction_config",
    "budget_config",
    "env",
    "debug_config",
    "model",
    "api_key",
    "vertex",
    "project",
    "location",
    "base_url",
})
"""Agent config fields agentpool sets itself (not passed through ``sdk_options``)."""


def antigravity_sdk_options(raw: Mapping[str, Any], *, openai: bool) -> dict[str, Any]:
    """Pass-through agent config fields, converted to the SDK's types.

    Args:
        raw: The fields as configured
        openai: Checked against ``LocalOpenAIAgentConfig`` (else ``LocalAgentConfig``)

    Raises:
        ValueError: An unknown or agentpool-managed field, or a value of the wrong type
    """
    if not raw:
        return {}
    from google.antigravity import LocalAgentConfig, LocalOpenAIAgentConfig

    from agentpool.agents.backend_options import check_backend_options, model_field_types

    config_type = LocalOpenAIAgentConfig if openai else LocalAgentConfig
    return check_backend_options(
        raw,
        model_field_types(config_type),
        managed=MANAGED_SDK_OPTIONS,
        backend=f"Antigravity {config_type.__name__}",
    )
