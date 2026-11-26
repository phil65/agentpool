"""Tests for Antigravity SDK -> AgentPool converters."""

from typing import TYPE_CHECKING

from mcp.types import ElicitResult
from pydantic_ai import BinaryContent, ImageUrl, TextContent

from agentpool.agents.antigravity_agent.converters import (
    elicitation_to_question_response,
    mcp_config_to_antigravity,
    question_to_elicitation,
    sanitize_mcp_server_name,
    steps_to_chat_messages,
    to_finish_reason,
    to_run_usage,
    user_content_to_antigravity,
)
from agentpool_config.mcp_server import (
    SSEMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)


if TYPE_CHECKING:
    from pathlib import Path


def test_sanitize_mcp_server_name() -> None:
    assert sanitize_mcp_server_name("my-server_1") == "my-server_1"
    assert sanitize_mcp_server_name("agentpool:tools.v1") == "agentpool_tools_v1"
    assert sanitize_mcp_server_name("!!!") == "mcp_server"


def test_mcp_config_conversion() -> None:
    from google.antigravity.types import McpStdioServer, McpStreamableHttpServer

    stdio = StdioMCPServerConfig(name="my.fs", command="npx", args=["-y", "server"], env={"A": "1"})
    converted_stdio = mcp_config_to_antigravity(stdio)
    assert isinstance(converted_stdio, McpStdioServer)
    assert converted_stdio.name == "my_fs"
    assert converted_stdio.command == "npx"
    assert converted_stdio.args == ["-y", "server"]
    assert converted_stdio.env == {"A": "1"}

    http = StreamableHTTPMCPServerConfig(
        name="remote", url="https://example.com/mcp", headers={"Authorization": "Bearer x"}
    )
    converted_http = mcp_config_to_antigravity(http)
    assert isinstance(converted_http, McpStreamableHttpServer)
    assert converted_http.url == "https://example.com/mcp"
    assert converted_http.headers == {"Authorization": "Bearer x"}

    sse = SSEMCPServerConfig(name="sse", url="https://example.com/sse", enabled=False)
    assert mcp_config_to_antigravity(sse) is None


def test_user_content_conversion(tmp_path: Path) -> None:
    from google.antigravity.types import Image

    assert user_content_to_antigravity(["hello"]) == "hello"
    assert user_content_to_antigravity([TextContent(content="hi")]) == "hi"

    png_bytes = b"\x89PNG\r\n\x1a\n"
    img_path = tmp_path / "pic.png"
    img_path.write_bytes(png_bytes)

    converted = user_content_to_antigravity([
        "Describe these",
        BinaryContent(data=png_bytes, media_type="image/png"),
        ImageUrl(url=img_path.as_uri()),
    ])
    assert isinstance(converted, list)
    assert converted[0] == "Describe these"
    assert isinstance(converted[1], Image)
    assert converted[1].data == png_bytes
    assert isinstance(converted[2], Image)
    assert converted[2].data == png_bytes


def test_usage_and_finish_reason() -> None:
    from google.antigravity.types import StopReason, UsageMetadata

    usage = UsageMetadata(
        prompt_token_count=100,
        cached_content_token_count=25,
        candidates_token_count=40,
        thoughts_token_count=15,
        total_token_count=155,
    )
    run_usage = to_run_usage(usage)
    assert run_usage.input_tokens == 100
    assert run_usage.cache_read_tokens == 25
    assert run_usage.output_tokens == 55
    assert run_usage.details == {"thoughts_tokens": 15}

    assert to_finish_reason(StopReason.UNSPECIFIED) == "stop"
    assert to_finish_reason(StopReason.MAX_OUTPUT_TOKENS_EXCEEDED) == "length"
    assert to_finish_reason(StopReason.QUOTA_EXHAUSTED) == "error"
    assert to_finish_reason(StopReason.UNSPECIFIED, failed=True) == "error"


def test_question_elicitation_roundtrip() -> None:
    from google.antigravity.types import AskQuestionEntry, AskQuestionOption

    entry = AskQuestionEntry(
        question="Pick a color",
        options=[
            AskQuestionOption(id="1", text="Red"),
            AskQuestionOption(id="2", text="Blue"),
        ],
    )
    params = question_to_elicitation(entry)
    assert params.message == "Pick a color"
    assert params.requested_schema["properties"]["answer"]["enum"] == ["Red", "Blue"]

    accepted = ElicitResult(action="accept", content={"answer": "Blue"})
    resp = elicitation_to_question_response(entry, accepted)
    assert resp.selected_option_ids == ["2"]
    assert not resp.skipped

    declined = ElicitResult(action="decline")
    resp_declined = elicitation_to_question_response(entry, declined)
    assert resp_declined.skipped is True


def test_steps_to_chat_messages() -> None:
    from google.antigravity.types import (
        Step,
        StepSource,
        StepStatus,
        StepTarget,
        StepType,
        ToolCall,
    )

    steps = [
        Step(
            id="t1:0",
            step_index=0,
            source=StepSource.USER,
            target=StepTarget.ENVIRONMENT,
            status=StepStatus.DONE,
            content="List files",
        ),
        Step(
            id="t1:1",
            step_index=1,
            type=StepType.TOOL_CALL,
            source=StepSource.MODEL,
            target=StepTarget.ENVIRONMENT,
            status=StepStatus.ACTIVE,
            tool_calls=[ToolCall(id="tc1", name="run_command", args={"CommandLine": "ls"})],
        ),
        Step(
            id="t1:1",
            step_index=1,
            type=StepType.TOOL_CALL,
            source=StepSource.MODEL,
            target=StepTarget.ENVIRONMENT,
            status=StepStatus.DONE,
            tool_calls=[ToolCall(id="tc1", name="run_command", args={"CommandLine": "ls"})],
        ),
        Step(
            id="t1:2",
            step_index=2,
            type=StepType.TEXT_RESPONSE,
            source=StepSource.MODEL,
            target=StepTarget.USER,
            status=StepStatus.DONE,
            thinking="Checking directory",
            content="Found main.py",
        ),
    ]
    messages = steps_to_chat_messages(
        steps, session_id="s1", agent_name="agy", model_name="gemini-4-hydrogen"
    )
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "List files"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Found main.py"
    assert len(messages[1].messages) == 2  # tool call beat + text/thinking beat
