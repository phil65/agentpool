"""Tests for message compaction pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
import pytest

from agentpool.messaging.compaction import (
    CompactionPipeline,
    FilterEmptyMessages,
    FilterRetryPrompts,
    FilterThinking,
    FilterToolCalls,
    KeepFirstAndLast,
    KeepFirstMessages,
    KeepLastMessages,
    TruncateTextParts,
    TruncateToolOutputs,
    WhenMessageCountExceeds,
    balanced_context,
    minimal_context,
)
from agentpool_config.compaction import (
    CompactionConfig,
    FilterThinkingConfig,
    KeepLastMessagesConfig,
    TruncateToolOutputsConfig,
    WhenMessageCountExceedsConfig,
)


if TYPE_CHECKING:
    from pydantic_ai import ModelMessage


@pytest.fixture
def sample_messages() -> list[ModelRequest | ModelResponse]:
    """Create a sample conversation for testing."""
    return [
        ModelRequest(parts=[UserPromptPart(content="Hello")]),
        ModelResponse(
            parts=[
                ThinkingPart(content="Let me think about this..."),
                TextPart(content="Hi there!"),
            ]
        ),
        ModelRequest(parts=[UserPromptPart(content="What is 2+2?")]),
        ModelResponse(parts=[TextPart(content="2+2 equals 4")]),
        ModelRequest(parts=[UserPromptPart(content="Thanks!")]),
        ModelResponse(parts=[TextPart(content="You're welcome!")]),
    ]


@pytest.fixture
def messages_with_tools() -> list[ModelRequest | ModelResponse]:
    """Create messages with tool calls."""
    return [
        ModelRequest(parts=[UserPromptPart(content="Search for Python docs")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="search", args={"query": "python"}, tool_call_id="call_1")
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search",
                    content="Python is a programming language...",
                    tool_call_id="call_1",
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Here's what I found about Python...")]),
    ]


async def test_filter_thinking(sample_messages):
    """Test that thinking parts are filtered out."""
    step = FilterThinking()
    result = await step.apply(sample_messages)
    # Check no thinking parts remain
    for msg in result:
        if isinstance(msg, ModelResponse):
            assert not any(isinstance(p, ThinkingPart) for p in msg.parts)

    # Original text should still be there
    assert any(
        isinstance(msg, ModelResponse)
        and any(isinstance(p, TextPart) and p.content == "Hi there!" for p in msg.parts)
        for msg in result
    )


async def test_filter_retry_prompts():
    """Test that retry prompts are filtered out."""
    messages: list[ModelMessage] = [
        ModelRequest(
            parts=[
                UserPromptPart(content="Do something"),
                ToolReturnPart(
                    tool_name="some_tool",
                    content="Please try again",
                    tool_call_id="call_1",
                    outcome="retried",
                ),
            ]
        ),
        ModelResponse(parts=[TextPart(content="Done")]),
    ]

    step = FilterRetryPrompts()
    result = await step.apply(messages)
    # Check no retry prompts remain
    for msg in result:
        if isinstance(msg, ModelRequest):
            assert not any(
                isinstance(p, ToolReturnPart) and p.outcome == "retried" for p in msg.parts
            )

    # User prompt should still be there
    assert any(
        isinstance(msg, ModelRequest) and any(isinstance(p, UserPromptPart) for p in msg.parts)
        for msg in result
    )


async def test_filter_tool_calls_exclude(messages_with_tools):
    """Test filtering specific tool calls by name."""
    step = FilterToolCalls(exclude_tools=["search"])
    result = await step.apply(messages_with_tools)
    # Check search tool calls are removed
    for msg in result:
        if isinstance(msg, ModelResponse):
            assert not any(
                isinstance(p, ToolCallPart) and p.tool_name == "search" for p in msg.parts
            )
        if isinstance(msg, ModelRequest):
            assert not any(
                isinstance(p, ToolReturnPart) and p.tool_name == "search" for p in msg.parts
            )


async def test_filter_tool_calls_include_only(messages_with_tools):
    """Test keeping only specific tools."""
    step = FilterToolCalls(include_only=["other_tool"])
    result = await step.apply(messages_with_tools)
    # Search tool should be filtered out
    for msg in result:
        if isinstance(msg, ModelResponse):
            assert not any(isinstance(p, ToolCallPart) for p in msg.parts)


async def test_filter_empty_messages():
    """Test that empty messages are removed."""
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="Hello")]),
        ModelResponse(parts=[TextPart(content="")]),  # Empty text
        ModelRequest(parts=[UserPromptPart(content="World")]),
        ModelResponse(parts=[TextPart(content="Response")]),
    ]
    step = FilterEmptyMessages()
    result = await step.apply(messages)
    # Empty response should be removed
    assert len(result) == 3


async def test_truncate_tool_outputs():
    """Test truncation of tool outputs."""
    long_content = "x" * 5000
    part = ToolReturnPart(tool_name="get_file", content=long_content, tool_call_id="1")
    messages = [ModelRequest(parts=[part])]
    step = TruncateToolOutputs(max_length=100)
    result = await step.apply(messages)
    # Content should be truncated
    request = result[0]
    assert isinstance(request, ModelRequest)
    tool_return = request.parts[0]
    assert isinstance(tool_return, ToolReturnPart)
    assert isinstance(tool_return.content, str)
    assert len(tool_return.content) < 150
    assert "[truncated]" in tool_return.content


async def test_truncate_text_parts():
    """Test truncation of text parts in responses."""
    long_content = "y" * 10000
    messages = [ModelResponse(parts=[TextPart(content=long_content)])]
    step = TruncateTextParts(max_length=200)
    result = await step.apply(messages)
    response = result[0]
    assert isinstance(response, ModelResponse)
    text_part = response.parts[0]
    assert isinstance(text_part, TextPart)
    assert len(text_part.content) < 250
    assert "[truncated]" in text_part.content


async def test_keep_last_messages(sample_messages):
    """Test keeping only last N messages."""
    step = KeepLastMessages(count=2, count_pairs=False)
    result = await step.apply(sample_messages)
    assert len(result) == 2
    # Should be the last two messages
    assert isinstance(result[0], ModelRequest)
    assert isinstance(result[1], ModelResponse)


async def test_keep_last_messages_pairs(sample_messages):
    """Test keeping last N message pairs."""
    step = KeepLastMessages(count=2, count_pairs=True)
    result = await step.apply(sample_messages)
    # 2 pairs = 4 messages (request + response each)
    assert len(result) == 4


async def test_keep_first_messages(sample_messages):
    """Test keeping only first N messages."""
    step = KeepFirstMessages(count=2)
    result = await step.apply(sample_messages)
    assert len(result) == 2
    # Should be the first two messages
    assert isinstance(result[0], ModelRequest)
    assert isinstance(result[1], ModelResponse)


async def test_keep_first_and_last(sample_messages):
    """Test keeping first and last messages."""
    step = KeepFirstAndLast(first_count=1, last_count=1)
    result = await step.apply(sample_messages)
    assert len(result) == 2
    # First should be initial request
    assert isinstance(result[0], ModelRequest)
    # Last should be final response
    assert isinstance(result[1], ModelResponse)


async def test_when_message_count_exceeds():
    """Test conditional step application."""
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="1")]),
        ModelResponse(parts=[TextPart(content="1")]),
    ]
    # Should not apply when below threshold
    step = WhenMessageCountExceeds(step=KeepLastMessages(count=1), threshold=5)
    result = await step.apply(messages)
    assert len(result) == 2
    # Should apply when above threshold
    many_messages = messages * 10  # 20 messages
    result = await step.apply(many_messages)
    assert len(result) < 20


async def test_pipeline_composition(sample_messages):
    """Test composing steps into a pipeline."""
    pipeline = CompactionPipeline(
        steps=[FilterThinking(), KeepLastMessages(count=2, count_pairs=True)]
    )
    result = await pipeline.apply(sample_messages)
    # Should have no thinking and only last 2 pairs
    assert len(result) == 4
    for msg in result:
        if isinstance(msg, ModelResponse):
            assert not any(isinstance(p, ThinkingPart) for p in msg.parts)


async def test_pipeline_operator_composition():
    """Test using | operator to compose steps."""
    step1 = FilterThinking()
    step2 = KeepLastMessages(count=2)
    pipeline = step1 | step2
    assert isinstance(pipeline, CompactionPipeline)
    assert len(pipeline.steps) == 2


async def test_config_roundtrip():
    """Test building pipeline from config."""
    config = CompactionConfig(
        steps=[
            FilterThinkingConfig(),
            TruncateToolOutputsConfig(max_length=500),
            KeepLastMessagesConfig(count=5),
        ]
    )

    pipeline = config.build()
    assert len(pipeline.steps) == 3
    assert isinstance(pipeline.steps[0], FilterThinking)
    assert isinstance(pipeline.steps[1], TruncateToolOutputs)
    assert isinstance(pipeline.steps[2], KeepLastMessages)


async def test_preset_minimal_context(sample_messages):
    """Test minimal context preset."""
    pipeline = minimal_context()
    result = await pipeline.apply(sample_messages)
    # Should be compacted
    assert len(result) <= len(sample_messages)
    # No thinking parts
    for msg in result:
        if isinstance(msg, ModelResponse):
            assert not any(isinstance(p, ThinkingPart) for p in msg.parts)


async def test_preset_balanced_context(sample_messages):
    """Test balanced context preset."""
    pipeline = balanced_context()
    result = await pipeline.apply(sample_messages)
    # Should process without errors
    assert len(result) <= len(sample_messages)


async def test_empty_messages_handling():
    """Test that pipeline handles empty message list."""
    pipeline = CompactionPipeline(steps=[FilterThinking(), KeepLastMessages(count=5)])
    result = await pipeline.apply([])
    assert result == []


async def test_config_with_nested_conditional():
    """Test config with nested conditional step."""
    step = WhenMessageCountExceedsConfig(threshold=10, step=KeepLastMessagesConfig(count=5))
    config = CompactionConfig(steps=[step])
    pipeline = config.build()
    assert len(pipeline.steps) == 1
    assert isinstance(pipeline.steps[0], WhenMessageCountExceeds)
