"""Tests for OpenCode V2 converters."""

from __future__ import annotations

from mcp.types import ElicitResult
from pydantic_ai import BinaryContent, ImageUrl
import pytest

from agentpool.agents.opencode_v2_agent.converters import (
    elicitation_to_form_answer,
    form_to_elicitation,
    from_usage_info,
    mcp_config_to_v2,
    parse_model_ref,
    to_finish_reason,
    to_mcp_server_status,
    to_run_usage,
    to_tokonomics_model_info,
    user_content_to_prompt_body,
    v2_messages_to_chat_messages,
)
from agentpool_config.mcp_server import StdioMCPServerConfig, StreamableHTTPMCPServerConfig
from opencode_v2_sdk.models import (
    FormBooleanField1,
    FormInfo1,
    FormMultiselectField1,
    FormOption,
    FormStringField1,
    McpLocalConfigEncoded,
    McpRemoteConfigEncoded,
    McpServer,
    McpStatusConnected,
    McpStatusFailed,
    ModelCapabilities,
    ModelInfo,
    ModelInfoLimit,
    ModelInfoTime,
    ModelRef,
    ModelVariant,
    PromptFileSourceInline,
    PromptFileSourceUri,
    SessionMessageAssistant,
    SessionMessageAssistantReasoning,
    SessionMessageAssistantText,
    SessionMessageAssistantTime,
    SessionMessageAssistantTool,
    SessionMessageAssistantToolTime,
    SessionMessageToolStateCompleted,
    SessionMessageUser,
    SessionMessageUserTime,
    TokenUsageInfo,
    TokenUsageInfoCache,
    ToolTextContent,
    UsageLimitsGo,
    UsageLimitsInfo,
    UsageLimitsInfoWorkspace,
    UsageLimitsMeter,
)


def _sample_model(provider_id: str, model_id: str, variants: list[str] | None = None) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        model_id=model_id,
        provider_id=provider_id,
        name=model_id,
        capabilities=ModelCapabilities(tools=True, input=["text", "image"], output=["text"]),
        variants=[ModelVariant(id=v) for v in (variants or [])],
        time=ModelInfoTime(released=0),
        cost=[],
        status="active",
        enabled=True,
        limit=ModelInfoLimit(context=128000, output=16000),
    )


def test_parse_model_ref_handles_slashed_ids_and_bare_ids() -> None:
    models = [
        _sample_model("openrouter", "thinkingmachines/inkling-2-mini", ["low", "high"]),
        _sample_model("anthropic", "claude-sonnet-4-5"),
    ]
    ref = parse_model_ref(
        "openrouter/thinkingmachines/inkling-2-mini",
        variant="high",
        available_models=models,
    )
    assert ref == ModelRef(
        id="thinkingmachines/inkling-2-mini",
        provider_id="openrouter",
        variant="high",
    )

    bare = parse_model_ref("claude-sonnet-4-5", available_models=models)
    assert bare == ModelRef(id="claude-sonnet-4-5", provider_id="anthropic")

    fallback = parse_model_ref("openai/gpt-5")
    assert fallback == ModelRef(id="gpt-5", provider_id="openai")

    with pytest.raises(ValueError, match="provider/model"):
        parse_model_ref("unknown-bare-model", available_models=models)


def test_to_tokonomics_model_info_and_usage() -> None:
    model = _sample_model("anthropic", "claude-sonnet-4-5", ["medium", "high"])
    info = to_tokonomics_model_info(model)
    assert info.id == "anthropic/claude-sonnet-4-5"
    assert info.provider == "anthropic"
    assert info.context_window == 128000
    assert info.input_modalities == {"text", "image"}
    assert info.metadata["variants"] == ["medium", "high"]

    tokens = TokenUsageInfo(
        input=100,
        output=40,
        reasoning=15,
        cache=TokenUsageInfoCache(read=30, write=10),
    )
    usage = to_run_usage(tokens)
    assert usage.input_tokens == 100
    assert usage.output_tokens == 40
    assert usage.cache_read_tokens == 30
    assert usage.cache_write_tokens == 10
    assert usage.details == {"reasoning_tokens": 15}

    assert to_finish_reason("tool-calls") == "tool_call"
    assert to_finish_reason("content-filter") == "content_filter"
    assert to_finish_reason("stop", failed=True) == "error"


def test_user_content_to_prompt_body() -> None:
    body = user_content_to_prompt_body([
        "Analyze this",
        BinaryContent(data=b"abc", media_type="image/png", identifier="shot.png"),
        ImageUrl(url="https://example.com/a.png"),
    ])
    assert body.text == "Analyze this"
    assert body.files is not None
    assert len(body.files) == 2
    assert isinstance(body.files[0].source, PromptFileSourceInline)
    assert body.files[0].uri.startswith("data:image/png;base64,")
    assert body.files[0].name == "shot.png"
    assert isinstance(body.files[1].source, PromptFileSourceUri)
    assert body.files[1].uri == "https://example.com/a.png"


def test_form_elicitation_roundtrip() -> None:
    form = FormInfo1(
        id="frm_1",
        session_id="ses_1",
        title="Configure deployment",
        fields=[
            FormStringField1(
                key="env",
                title="Environment",
                required=True,
                options=[
                    FormOption(value="stg", label="Staging"),
                    FormOption(value="prd", label="Production"),
                ],
            ),
            FormMultiselectField1(
                key="regions",
                title="Regions",
                options=[
                    FormOption(value="us", label="US East"),
                    FormOption(value="eu", label="EU West"),
                ],
            ),
            FormBooleanField1(key="dry_run", title="Dry Run", default=True),
        ],
    )
    params = form_to_elicitation(form)
    assert params.message == "Configure deployment"
    assert params.requested_schema["required"] == ["env"]
    assert params.requested_schema["properties"]["env"]["enum"] == ["Staging", "Production"]
    assert params.requested_schema["properties"]["regions"]["type"] == "array"

    accepted = ElicitResult(
        action="accept",
        content={"env": "Production", "regions": ["US East", "EU West"], "dry_run": False},
    )
    assert elicitation_to_form_answer(form, accepted) == {
        "env": "prd",
        "regions": ["us", "eu"],
        "dry_run": False,
    }
    assert elicitation_to_form_answer(form, ElicitResult(action="cancel")) is None


def test_v2_messages_to_chat_messages() -> None:
    raw = [
        SessionMessageUser(
            id="msg_1",
            time=SessionMessageUserTime(created=1000),
            text="Run pwd",
        ),
        SessionMessageAssistant(
            id="msg_2",
            time=SessionMessageAssistantTime(created=2000),
            agent="build",
            model=ModelRef(id="gpt-5", provider_id="openai"),
            content=[
                SessionMessageAssistantReasoning(text="Running pwd"),
                SessionMessageAssistantTool(
                    id="call_1",
                    name="bash",
                    state=SessionMessageToolStateCompleted(
                        input={"command": "pwd"},
                        content=[ToolTextContent(text="/work")],
                    ),
                    time=SessionMessageAssistantToolTime(created=2100),
                ),
                SessionMessageAssistantText(text="We are in /work."),
            ],
            finish="stop",
        ),
    ]
    converted = v2_messages_to_chat_messages(raw, session_id="ses_1", agent_name="oc")
    assert len(converted) == 2
    assert converted[0].role == "user"
    assert converted[0].content == "Run pwd"
    assert converted[1].role == "assistant"
    assert converted[1].content == "We are in /work."
    assert converted[1].model_name == "openai/gpt-5"
    assert len(converted[1].messages) == 3


def test_mcp_and_usage_limits_converters() -> None:
    stdio = StdioMCPServerConfig(name="my.server", command="uvx", args=["mcp-fs"])
    pair = mcp_config_to_v2(stdio)
    assert pair is not None
    assert pair[0] == "my_server"
    assert isinstance(pair[1], McpLocalConfigEncoded)
    assert pair[1].command == ["uvx", "mcp-fs"]

    http = StreamableHTTPMCPServerConfig(name="bridge", url="http://127.0.0.1:9000/mcp")
    http_pair = mcp_config_to_v2(http)
    assert http_pair is not None
    assert isinstance(http_pair[1], McpRemoteConfigEncoded)
    assert http_pair[1].url == "http://127.0.0.1:9000/mcp"

    connected = to_mcp_server_status(McpServer(name="a", status=McpStatusConnected()))
    assert connected.status == "connected"
    failed = to_mcp_server_status(McpServer(name="b", status=McpStatusFailed(error="boom")))
    assert failed.status == "failed"
    assert failed.error == "boom"

    limits_info = UsageLimitsInfo(
        workspace=UsageLimitsInfoWorkspace(id="ws", name="Pro Plan", usage_url="https://x"),
        go=UsageLimitsGo(
            rolling=UsageLimitsMeter(percent=25, limited=False, resets_at=0),
            weekly=UsageLimitsMeter(percent=50, limited=False, resets_at=0),
            monthly=UsageLimitsMeter(percent=10, limited=False, resets_at=0),
        ),
        budgets=[],
    )
    limits = from_usage_info(limits_info)
    assert limits is not None
    assert limits.plan == "Pro Plan"
    assert [w.percent for w in limits.windows] == [25.0, 50.0, 10.0]
