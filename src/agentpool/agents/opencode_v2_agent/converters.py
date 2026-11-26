"""Converters between OpenCode V2 SDK models and AgentPool types."""

import base64
from datetime import UTC, datetime
import re
from typing import TYPE_CHECKING, Any, assert_never

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

from agentpool.agents.usage_limits import UsageLimits, UsagePeriod, UsageWindow
from agentpool.common_types import MCPServerStatus
from agentpool.messaging import ChatMessage
from agentpool.sessions import SessionData
from agentpool.utils.pydantic_ai_helpers import get_builtin_tool_parts
from opencode_v2_sdk.models import (
    FormBooleanField1,
    FormExternalField,
    FormIntegerField1,
    FormMultiselectField1,
    FormNumberField1,
    FormStringField1,
    McpLocalConfigEncoded,
    McpRemoteConfigEncoded,
    McpStatusConnected,
    McpStatusDisabled,
    McpStatusFailed,
    McpStatusNeedsAuth,
    McpStatusPending,
    ModelRef,
    PromptFileSourceInline,
    PromptFileSourceUri,
    PromptInputFileAttachment,
    SessionMessageAssistant,
    SessionMessageAssistantReasoning,
    SessionMessageAssistantText,
    SessionMessageAssistantTool,
    SessionMessageToolStateCompleted,
    SessionMessageToolStateError,
    SessionMessageToolStateRunning,
    SessionMessageToolStateStreaming,
    SessionMessageUser,
    SessionPromptBody,
    ToolFileContent,
    ToolTextContent,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp.types import ErrorData
    from pydantic_ai import FinishReason, UserContent
    from tokonomics.model_discovery.model_info import Modality, ModelInfo as TokoModelInfo

    from agentpool_config.mcp_server import MCPServerConfig
    from opencode_v2_sdk.client import FormAnswerValue
    from opencode_v2_sdk.models import (
        FormInfo1,
        McpServer,
        ModelInfo,
        SessionInfo,
        SessionMessageInfo,
        TokenUsageInfo,
        UsageLimitsInfo,
        UsageLimitsMeter,
    )


_MCP_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_MODALITY_MAP: dict[str, Modality] = {
    "text": "text",
    "image": "image",
    "audio": "audio",
    "video": "video",
    "pdf": "file",
    "file": "file",
}


def sanitize_mcp_name(name: str) -> str:
    """Normalize an MCP server identifier for OpenCode V2 routes."""
    sanitized = _MCP_NAME_RE.sub("_", name).strip("_")
    return sanitized or "mcp_server"


def format_model_ref(ref: ModelRef) -> str:
    """Format a ``ModelRef`` as ``provider/model``."""
    return f"{ref.provider_id}/{ref.id}"


def parse_model_ref(
    value: str,
    *,
    variant: str | None = None,
    available_models: Sequence[ModelInfo] | None = None,
) -> ModelRef:
    """Resolve a model string (``provider/model`` or bare model ID) into a ``ModelRef``.

    When ``available_models`` is provided, checks exact ``provider/id`` matches first
    (handling model IDs that contain slashes such as OpenRouter IDs), then bare ``id``
    or ``model_id`` matches, before falling back to splitting on the first ``/``.
    """
    if available_models:
        for model in available_models:
            if value == f"{model.provider_id}/{model.id}":
                return ModelRef(id=model.id, provider_id=model.provider_id, variant=variant)
        for model in available_models:
            if value in (model.id, model.model_id):
                return ModelRef(id=model.id, provider_id=model.provider_id, variant=variant)

    provider_id, sep, model_id = value.partition("/")
    if sep and provider_id and model_id:
        return ModelRef(id=model_id, provider_id=provider_id, variant=variant)
    msg = f"OpenCode V2 model must be given as 'provider/model' or match /api/model, got {value!r}"
    raise ValueError(msg)


def to_tokonomics_model_info(model: ModelInfo) -> TokoModelInfo:
    """Convert an OpenCode V2 ``ModelInfo`` to ``tokonomics.ModelInfo``."""
    from tokonomics.model_discovery.model_info import ModelInfo as TokoModelInfo

    full_id = f"{model.provider_id}/{model.id}"
    inputs: set[Modality] = {
        _MODALITY_MAP[m] for m in model.capabilities.input if m in _MODALITY_MAP
    } or {"text"}
    outputs: set[Modality] = {
        _MODALITY_MAP[m] for m in model.capabilities.output if m in _MODALITY_MAP
    } or {"text"}
    context_window = (
        int(model.limit.context) if isinstance(model.limit.context, (int, float)) else None
    )
    return TokoModelInfo(
        id=full_id,
        name=model.name or model.id,
        provider=model.provider_id,
        context_window=context_window,
        input_modalities=inputs,
        output_modalities=outputs,
        id_override=full_id,
        metadata={
            "model_id": model.id,
            "variants": [v.id for v in model.variants],
        },
    )


def to_run_usage(tokens: TokenUsageInfo | None) -> RunUsage:
    """Convert OpenCode V2 ``TokenUsageInfo`` to pydantic-ai ``RunUsage``."""
    if tokens is None:
        return RunUsage()
    input_tokens = int(tokens.input) if isinstance(tokens.input, (int, float)) else 0
    output_tokens = int(tokens.output) if isinstance(tokens.output, (int, float)) else 0
    reasoning = int(tokens.reasoning) if isinstance(tokens.reasoning, (int, float)) else 0
    cache_read = int(tokens.cache.read) if isinstance(tokens.cache.read, (int, float)) else 0
    cache_write = int(tokens.cache.write) if isinstance(tokens.cache.write, (int, float)) else 0
    details = {"reasoning_tokens": reasoning} if reasoning else {}
    return RunUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        details=details,
    )


def to_request_usage(tokens: TokenUsageInfo | None) -> RequestUsage:
    """Convert OpenCode V2 ``TokenUsageInfo`` to pydantic-ai ``RequestUsage``."""
    run = to_run_usage(tokens)
    return RequestUsage(
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cache_read_tokens=run.cache_read_tokens,
        cache_write_tokens=run.cache_write_tokens,
        details=dict(run.details),
    )


_FINISH_REASON_MAP: dict[str, FinishReason] = {
    "stop": "stop",
    "unknown": "stop",
    "length": "length",
    "tool-calls": "tool_call",
    "content-filter": "content_filter",
    "error": "error",
}


def to_finish_reason(finish: str | None, *, failed: bool = False) -> FinishReason:
    """Convert an OpenCode V2 finish reason string to pydantic-ai ``FinishReason``."""
    if failed:
        return "error"
    return _FINISH_REASON_MAP.get(finish or "stop", "stop")


def user_content_to_prompt_body(
    prompts: Sequence[UserContent],
    *,
    delivery: str | None = None,
) -> SessionPromptBody:
    """Convert pydantic-ai ``UserContent`` prompts into a V2 ``SessionPromptBody``."""
    text_parts: list[str] = []
    files: list[PromptInputFileAttachment] = []

    for item in prompts:
        match item:
            case str(text) | TextContent(content=text):
                if text:
                    text_parts.append(text)
            case BinaryContent(data=data, media_type=media_type, identifier=ident):
                b64 = base64.b64encode(data).decode("ascii")
                data_uri = f"data:{media_type};base64,{b64}"
                files.append(
                    PromptInputFileAttachment(
                        uri=data_uri,
                        source=PromptFileSourceInline(),
                        name=ident,
                    )
                )
            case ImageUrl(url=url) | DocumentUrl(url=url) | AudioUrl(url=url) | VideoUrl(url=url):
                files.append(
                    PromptInputFileAttachment(
                        uri=url,
                        source=PromptFileSourceUri(uri=url),
                    )
                )
            case CachePoint() | UploadedFile():
                pass
            case _ as unreachable:
                assert_never(unreachable)

    return SessionPromptBody(
        text="\n\n".join(text_parts),
        files=files or None,
        delivery="queue" if delivery == "queue" else "steer",
        resume=True,
    )


def format_tool_content(
    content: Sequence[ToolTextContent | ToolFileContent] | None,
) -> str:
    """Format OpenCode V2 tool output content items into a plain string."""
    if not content:
        return ""
    parts: list[str] = []
    for item in content:
        match item:
            case ToolTextContent(text=text):
                if text:
                    parts.append(text)
            case ToolFileContent(uri=uri, name=name, mime=mime):
                label = name or uri
                parts.append(f"[{mime}: {label}]")
            case _ as unreachable:
                assert_never(unreachable)
    return "\n".join(parts)


def mcp_config_to_v2(
    config: MCPServerConfig,
) -> tuple[str, McpLocalConfigEncoded | McpRemoteConfigEncoded] | None:
    """Convert an AgentPool ``MCPServerConfig`` to an OpenCode V2 MCP payload."""
    from agentpool_config.mcp_server import (
        SSEMCPServerConfig,
        StdioMCPServerConfig,
        StreamableHTTPMCPServerConfig,
    )

    if not config.enabled:
        return None
    name = sanitize_mcp_name(config.name or f"server_{id(config)}")
    match config:
        case StdioMCPServerConfig(command=command, args=args, env=env):
            return (
                name,
                McpLocalConfigEncoded(
                    command=[command, *args],
                    environment=dict(env) if env else None,
                ),
            )
        case StreamableHTTPMCPServerConfig(url=url, headers=headers):
            return (
                name,
                McpRemoteConfigEncoded(
                    url=str(url),
                    headers=dict(headers) if headers else None,
                    oauth=False,
                ),
            )
        case SSEMCPServerConfig(url=url, headers=headers):
            return (
                name,
                McpRemoteConfigEncoded(
                    url=str(url),
                    headers=dict(headers) if headers else None,
                    oauth=False,
                ),
            )
        case _ as unreachable:
            assert_never(unreachable)


def to_mcp_server_status(server: McpServer) -> MCPServerStatus:
    """Convert an OpenCode V2 ``McpServer`` entry to AgentPool's ``MCPServerStatus``."""
    match server.status:
        case McpStatusConnected():
            return MCPServerStatus(name=server.name, status="connected", server_name=server.title)
        case McpStatusPending():
            return MCPServerStatus(name=server.name, status="pending", server_name=server.title)
        case McpStatusDisabled():
            return MCPServerStatus(name=server.name, status="disabled", server_name=server.title)
        case McpStatusNeedsAuth(error=err):
            return MCPServerStatus(
                name=server.name, status="needs-auth", error=err, server_name=server.title
            )
        case McpStatusFailed(error=err):
            return MCPServerStatus(
                name=server.name, status="failed", error=err, server_name=server.title
            )
        case _ as unreachable:
            assert_never(unreachable)


def to_session_data(info: SessionInfo, *, agent_name: str) -> SessionData:
    """Convert an OpenCode V2 ``SessionInfo`` to AgentPool ``SessionData``."""
    created_ms = info.time.created if isinstance(info.time.created, (int, float)) else 0
    updated_ms = info.time.updated if isinstance(info.time.updated, (int, float)) else created_ms
    created_at = datetime.fromtimestamp(created_ms / 1000.0, tz=UTC)
    last_active = datetime.fromtimestamp(updated_ms / 1000.0, tz=UTC)
    metadata: dict[str, Any] = {}
    if info.title:
        metadata["title"] = info.title
    if info.agent:
        metadata["opencode_agent"] = info.agent
    return SessionData(
        session_id=info.id,
        agent_name=agent_name,
        cwd=info.location.directory,
        sdk_session_id=info.id,
        created_at=created_at,
        last_active=last_active,
        metadata=metadata,
    )


def v2_messages_to_chat_messages(
    messages: Sequence[SessionMessageInfo],
    *,
    session_id: str,
    agent_name: str,
) -> list[ChatMessage[Any]]:
    """Convert OpenCode V2 projected ``SessionMessageInfo`` entries to ``ChatMessage``s."""
    result: list[ChatMessage[Any]] = []
    for msg in messages:
        match msg:
            case SessionMessageUser(id=msg_id, text=text):
                request = ModelRequest(parts=[UserPromptPart(content=text)])
                result.append(
                    ChatMessage[str](
                        content=text,
                        role="user",
                        message_id=msg_id,
                        session_id=session_id,
                        messages=[request],
                    )
                )
            case SessionMessageAssistant(
                id=msg_id,
                model=model_ref,
                content=parts,
                finish=finish,
                tokens=tokens,
                error=error,
            ):
                model_name = format_model_ref(model_ref)
                texts: list[str] = []
                responses: list[ModelRequest | ModelResponse] = []
                for part in parts:
                    match part:
                        case SessionMessageAssistantText(text=text):
                            if text:
                                texts.append(text)
                                responses.append(
                                    ModelResponse(
                                        parts=[TextPart(content=text)],
                                        model_name=model_name,
                                    )
                                )
                        case SessionMessageAssistantReasoning(text=text):
                            if text:
                                responses.append(
                                    ModelResponse(
                                        parts=[ThinkingPart(content=text)],
                                        model_name=model_name,
                                    )
                                )
                        case SessionMessageAssistantTool(id=tc_id, name=tool_name, state=state):
                            args, output = _tool_state_args_and_output(state)
                            tool_parts = get_builtin_tool_parts(
                                tool_name=tool_name,
                                args=args,
                                tc_id=tc_id,
                                content=output,
                            )
                            responses.append(ModelResponse(parts=tool_parts, model_name=model_name))
                        case _ as unreachable:
                            assert_never(unreachable)

                result.append(
                    ChatMessage[str](
                        content="\n\n".join(texts),
                        role="assistant",
                        name=agent_name,
                        message_id=msg_id,
                        session_id=session_id,
                        model_name=model_name,
                        usage=to_run_usage(tokens),
                        messages=responses,
                        finish_reason=to_finish_reason(finish, failed=error is not None),
                    )
                )
            case _:
                continue
    return result


def _tool_state_args_and_output(
    state: (
        SessionMessageToolStateStreaming
        | SessionMessageToolStateRunning
        | SessionMessageToolStateCompleted
        | SessionMessageToolStateError
    ),
) -> tuple[dict[str, Any], str]:
    match state:
        case SessionMessageToolStateCompleted(input=args, content=content):
            return dict(args), format_tool_content(content)
        case SessionMessageToolStateError(input=args, error=err, content=content):
            text = format_tool_content(content)
            return dict(args), text or err.message
        case SessionMessageToolStateRunning(input=args):
            return dict(args), ""
        case SessionMessageToolStateStreaming(input=raw):
            return {"input": raw}, ""
        case _ as unreachable:
            assert_never(unreachable)


def form_to_elicitation(form: FormInfo1) -> ElicitRequestFormParams:
    """Convert an OpenCode V2 ``FormInfo1`` into an MCP ``ElicitRequestFormParams``."""
    properties: dict[str, Any] = {}
    required: list[str] = []

    for fld in form.fields:
        prop: dict[str, Any] = {}
        if fld.title:
            prop["title"] = fld.title
        if fld.description:
            prop["description"] = fld.description
        match fld:
            case FormStringField1(options=options, default=default):
                prop["type"] = "string"
                if options:
                    prop["enum"] = [opt.label for opt in options]
                if default is not None:
                    prop["default"] = default
            case FormNumberField1(default=default):
                prop["type"] = "number"
                if isinstance(default, (int, float)):
                    prop["default"] = default
            case FormIntegerField1(default=default):
                prop["type"] = "integer"
                if default is not None:
                    prop["default"] = default
            case FormBooleanField1(default=default):
                prop["type"] = "boolean"
                if default is not None:
                    prop["default"] = default
            case FormMultiselectField1(options=options, default=default):
                prop["type"] = "array"
                prop["items"] = {"type": "string", "enum": [opt.label for opt in options]}
                if default is not None:
                    prop["default"] = list(default)
            case FormExternalField(url=url):
                prop["type"] = "string"
                prop["description"] = f"{fld.description or ''} ({url})".strip()
            case _ as unreachable:
                assert_never(unreachable)

        properties[fld.key] = prop
        if not isinstance(fld, FormExternalField) and fld.required:
            required.append(fld.key)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return ElicitRequestFormParams(message=form.title, requested_schema=schema)


def elicitation_to_form_answer(
    form: FormInfo1,
    result: ElicitResult | ErrorData,
) -> dict[str, FormAnswerValue] | None:
    """Convert an ``InputProvider.get_elicitation`` result to an OpenCode V2 form answer."""
    if not isinstance(result, ElicitResult) or result.action != "accept" or not result.content:
        return None

    answer: dict[str, FormAnswerValue] = {}
    for fld in form.fields:
        if fld.key not in result.content:
            continue
        raw = result.content[fld.key]
        if raw is None:
            continue
        match fld:
            case FormStringField1(options=options):
                by_label = {opt.label: opt.value for opt in options or []}
                text = str(raw)
                answer[fld.key] = by_label.get(text, text)
            case FormMultiselectField1(options=options):
                by_label = {opt.label: opt.value for opt in options}
                items = raw if isinstance(raw, list) else [raw]
                answer[fld.key] = [by_label.get(str(v), str(v)) for v in items]
            case FormBooleanField1():
                answer[fld.key] = bool(raw)
            case FormIntegerField1():
                answer[fld.key] = int(raw) if isinstance(raw, (int, float, str)) else 0
            case FormNumberField1():
                answer[fld.key] = float(raw) if isinstance(raw, (int, float, str)) else 0.0
            case FormExternalField():
                answer[fld.key] = str(raw)
            case _ as unreachable:
                assert_never(unreachable)
    return answer


def _meter_to_window(period: UsagePeriod, label: str, meter: UsageLimitsMeter) -> UsageWindow:
    percent = float(meter.percent) if isinstance(meter.percent, (int, float)) else 0.0
    resets_at = (
        datetime.fromtimestamp(meter.resets_at / 1000.0, tz=UTC)
        if isinstance(meter.resets_at, (int, float)) and meter.resets_at > 0
        else None
    )
    return UsageWindow(
        id=period,
        label=label,
        period=period,
        percent=percent,
        limited=meter.limited,
        resets_at=resets_at,
    )


def from_usage_info(info: UsageLimitsInfo | None) -> UsageLimits | None:
    """Convert OpenCode V2 ``UsageLimitsInfo`` into AgentPool ``UsageLimits``."""
    if info is None or info.go is None:
        return None
    windows = [
        _meter_to_window("session", "Session", info.go.rolling),
        _meter_to_window("weekly", "Weekly", info.go.weekly),
        _meter_to_window("monthly", "Monthly", info.go.monthly),
    ]
    return UsageLimits(
        plan=info.workspace.name or None,
        usage_url=info.workspace.usage_url or None,
        windows=windows,
    )
