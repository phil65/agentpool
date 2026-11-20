"""Tests for builtin event handlers."""

from __future__ import annotations

from io import StringIO
import sys
from typing import Any

from pydantic_ai import PartStartEvent, TextPart
import pytest

from agentpool.agents.events import (
    RunErrorEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
    detailed_print_handler,
    simple_print_handler,
)
from agentpool.messaging import ChatMessage


@pytest.fixture
def mock_run_context():
    """Create a mock RunContext for testing."""

    class MockRunContext:
        def __init__(self):
            self.deps = None
            self.tool_name = "test_tool"
            self.tool_call_id = "test_call_123"

    return MockRunContext()


async def test_simple_print_handler_text(mock_run_context):
    """Test simple handler prints text content."""
    output = StringIO()
    sys.stderr = output
    event = PartStartEvent(part=TextPart(content="Hello world"), index=0)
    try:
        await simple_print_handler(mock_run_context, event)
        assert output.getvalue() == "Hello world"
    finally:
        sys.stderr = sys.__stderr__


def _complete(
    tool_name: str, result: Any, *, tool_input: dict[str, Any] | None = None
) -> ToolCallCompleteEvent:
    return ToolCallCompleteEvent(
        tool_name=tool_name,
        tool_call_id="call_123",
        tool_input=tool_input or {},
        tool_result=result,
        agent_name="test",
        message_id="m1",
    )


async def test_simple_print_handler_tool_call(mock_run_context):
    """Test simple handler prints tool call."""
    output = StringIO()
    sys.stderr = output
    event = _complete("test_tool", "done", tool_input={"param": "value"})
    try:
        await simple_print_handler(mock_run_context, event)
        assert "🔧 test_tool(param='value')" in output.getvalue()
    finally:
        sys.stderr = sys.__stderr__


async def test_simple_print_handler_error(mock_run_context):
    """Test simple handler prints errors."""
    output = StringIO()
    sys.stderr = output
    event = RunErrorEvent(message="Something went wrong", code="ERR001")
    try:
        await simple_print_handler(mock_run_context, event)
        result = output.getvalue()
        assert "❌" in result
        assert "Something went wrong" in result
    finally:
        sys.stderr = sys.__stderr__


async def test_detailed_print_handler_tool_call(mock_run_context):
    """Test detailed handler prints tool call with inputs."""
    output = StringIO()
    sys.stderr = output
    event = ToolCallStartEvent(
        tool_call_id="call_123", tool_name="test_tool", title="Testing something"
    )
    try:
        await detailed_print_handler(mock_run_context, event)
        result = output.getvalue()
        assert "🔧 test_tool" in result
        assert "Testing something" in result
        assert "call_123"[:8] in result
    finally:
        sys.stderr = sys.__stderr__


async def test_detailed_print_handler_tool_result(mock_run_context):
    """Test detailed handler prints tool results."""
    output = StringIO()
    sys.stderr = output
    event = _complete("test_tool", "Op successful")
    try:
        await detailed_print_handler(mock_run_context, event)
        result = output.getvalue()
        assert "✅" in result
        assert "test_tool" in result
        assert "Op successful" in result
    finally:
        sys.stderr = sys.__stderr__


async def test_detailed_print_handler_truncates_long_output(mock_run_context):
    """Test detailed handler truncates long outputs."""
    output = StringIO()
    sys.stderr = output
    long_content = "x" * 200
    event = _complete("test_tool", long_content)
    try:
        await detailed_print_handler(mock_run_context, event)
        result = output.getvalue()
        assert "..." in result
        assert len(result) < len(long_content)
    finally:
        sys.stderr = sys.__stderr__


async def test_stream_complete_adds_newline(mock_run_context):
    """Test both handlers add final newline on stream complete."""
    for handler in [simple_print_handler, detailed_print_handler]:
        output = StringIO()
        sys.stderr = output
        message = ChatMessage(content="test", role="user")
        event = StreamCompleteEvent(message=message)
        try:
            await handler(mock_run_context, event)
            assert output.getvalue() == "\n"
        finally:
            sys.stderr = sys.__stderr__
