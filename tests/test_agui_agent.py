"""Tests for AG-UI remote agent."""

from __future__ import annotations

import socket
import sys
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from ag_ui.core import TextMessageContentEvent, ToolCallStartEvent
import anyio
import httpx2
from pydantic_ai import PartDeltaEvent
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool
from agentpool.agents.agui_agent import AGUIAgent
from agentpool.agents.agui_agent.agui_converters import agui_to_native_event, to_agui_tool
from agentpool.agents.events import StreamCompleteEvent, ToolCallStartEvent as NativeToolCallStart
from agentpool.agents.tool_call_accumulator import ToolCallAccumulator
from agentpool.messaging import ChatMessage
from agentpool.talk.stats import MessageStats
from agentpool.tools import FunctionTool
from agentpool_server.agui_server import AGUIServer


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
def mock_sse_response():
    """Create mock SSE response."""

    def _create_response(events: list[str]) -> AsyncMock:
        """Create mock response with SSE events."""
        response = AsyncMock(spec=httpx2.Response)
        response.raise_for_status = MagicMock()

        async def aiter_text() -> AsyncIterator[str]:
            for event in events:
                yield event

        response.aiter_text = aiter_text
        return response

    return _create_response


async def test_agui_agent_initialization():
    """Test AGUIAgent initialization."""
    agent = AGUIAgent(endpoint="http://localhost:8000/run", name="test-agent")
    assert agent.endpoint == "http://localhost:8000/run"
    assert agent.name == "test-agent"
    # session_id is None until first run (lazy initialization)
    assert agent.session_id is None
    assert agent._client is None
    assert agent._sdk_session_id is None


async def test_agui_agent_context_manager():
    """Test AGUIAgent context manager."""
    async with AGUIAgent(endpoint="http://localhost:8000/run", name="test-agent") as agent:
        assert agent._client is not None
        # _thread_id is None until first run (lazy initialization of session_id)
        assert agent._sdk_session_id is None

    # After exit, should be cleaned up
    assert agent._client is None
    assert agent._sdk_session_id is None


async def test_agui_agent_run_stream(mock_sse_response):
    """Test AGUIAgent streaming execution."""
    # Create mock SSE events
    events = [
        'data: {"type":"TEXT_MESSAGE_START","messageId":"msg1"}\n\n',
        'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"msg1","delta":"Hello"}\n\n',
        'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"msg1","delta":" world"}\n\n',
        'data: {"type":"TEXT_MESSAGE_END","messageId":"msg1"}\n\n',
    ]

    mock_response = mock_sse_response(events)

    with patch("agentpool.agents.agui_agent.agui_agent.httpx2.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        # stream() should return an async context manager
        mock_stream_cm = MagicMock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)
        async with AGUIAgent(endpoint="http://localhost:8000/run", name="test-agent") as agent:
            collected_events = [event async for event in agent.run_stream("Test prompt")]
            # Should have text deltas and final message
            assert len(collected_events) > 0
            # Final event should be StreamCompleteEvent with ChatMessage
            final_event = collected_events[-1]
            assert isinstance(final_event, StreamCompleteEvent)
            assert final_event.message.content == "Hello world"


async def test_agui_agent_run(mock_sse_response):
    """Test AGUIAgent non-streaming execution."""
    events = [
        'data: {"type":"TEXT_MESSAGE_START","messageId":"msg1"}\n\n',
        'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"msg1","delta":"Result"}\n\n',
        'data: {"type":"TEXT_MESSAGE_END","messageId":"msg1"}\n\n',
    ]

    mock_response = mock_sse_response(events)
    with patch("agentpool.agents.agui_agent.agui_agent.httpx2.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        # stream() should return an async context manager
        mock_stream_cm = MagicMock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        async with AGUIAgent(endpoint="http://localhost:8000/run", name="test-agent") as agent:
            result = await agent.run("Test prompt")
            assert isinstance(result, ChatMessage)
            assert result.content == "Result"
            assert result.role == "assistant"
            assert result.name == "test-agent"


def test_agui_to_native_event_text_content():
    """Test conversion of text content events."""
    event = TextMessageContentEvent(message_id="msg1", delta="Test content")
    native = next(agui_to_native_event(event))
    assert native is not None
    assert isinstance(native, PartDeltaEvent)


def test_agui_to_native_event_tool_call():
    """Test conversion of tool call events."""
    event = ToolCallStartEvent(tool_call_id="call1", tool_call_name="test_tool")
    native = next(agui_to_native_event(event))
    assert native is not None
    assert isinstance(native, NativeToolCallStart)
    assert native.tool_call_id == "call1"
    assert native.tool_name == "test_tool"


async def test_agui_agent_to_tool():
    """Test converting AGUIAgent to a tool."""
    events = [
        'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"msg1","delta":"Answer"}\n\n',
    ]

    with patch("agentpool.agents.agui_agent.agui_agent.httpx2.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        mock_response = AsyncMock(spec=httpx2.Response)
        mock_response.raise_for_status = MagicMock()

        async def aiter_text():
            for event in events:
                yield event

        mock_response.aiter_text = aiter_text

        # stream() should return an async context manager
        mock_stream_cm = MagicMock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        async with AGUIAgent(endpoint="http://localhost:8000/run", name="test-agent") as agent:
            tool = agent.to_tool(description="Test tool description")
            assert callable(tool.fn)
            assert tool.name == "ask_test-agent"
            assert "Test tool description" in tool.description

            result = await tool.run(prompt="Test question")
            assert result == "Answer"


async def test_agui_agent_get_stats():
    """Test getting agent statistics."""
    async with AGUIAgent(endpoint="http://localhost:8000/run", name="test-agent") as agent:
        stats = await agent.get_stats()
        assert isinstance(stats, MessageStats)
        assert stats.message_count == 0
        assert isinstance(stats.messages, list)


@pytest.mark.skipif(sys.platform == "win32", reason="Hangs on Windows CI")
async def test_agui_agent_error_handling(mock_sse_response):
    """Test error handling in AGUIAgent."""
    with patch("agentpool.agents.agui_agent.agui_agent.httpx2.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        # Simulate HTTP error
        mock_response = AsyncMock(spec=httpx2.Response)
        mock_response.raise_for_status.side_effect = httpx2.HTTPStatusError(
            "Server error",
            request=MagicMock(),
            response=MagicMock(),
        )

        # stream() should return an async context manager
        mock_stream_cm = MagicMock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)
        async with AGUIAgent(endpoint="http://localhost:8000/run", name="test-agent") as agent:
            with pytest.raises(httpx2.HTTPStatusError):
                async for _ in agent.run_stream("Test"):
                    pass


async def test_agui_agent_with_tools(mock_sse_response):
    """Test AGUIAgent with client-side tool execution."""

    # Define a simple test tool
    def test_calculator(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    # Create mock SSE events that include tool calls
    events = [
        'data: {"type":"TEXT_MESSAGE_START","messageId":"msg1"}\n\n',
        'data: {"type":"TOOL_CALL_START","toolCallId":"call1","toolCallName":"test_calculator"}\n\n',  # noqa: E501
        'data: {"type":"TOOL_CALL_ARGS","toolCallId":"call1","delta":"{\\"x\\": 5"}\n\n',
        'data: {"type":"TOOL_CALL_ARGS","toolCallId":"call1","delta":", \\"y\\": 3}"}\n\n',
        'data: {"type":"TOOL_CALL_END","toolCallId":"call1"}\n\n',
        'data: {"type":"TEXT_MESSAGE_END","messageId":"msg1"}\n\n',
    ]

    # Second response after tool execution (with tool results)
    final_events = [
        'data: {"type":"TEXT_MESSAGE_START","messageId":"msg2"}\n\n',
        'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"msg2","delta":"The result is 8"}\n\n',
        'data: {"type":"TEXT_MESSAGE_END","messageId":"msg2"}\n\n',
    ]

    with patch("agentpool.agents.agui_agent.agui_agent.httpx2.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        # Create mock responses
        mock_response1 = mock_sse_response(events)
        mock_response2 = mock_sse_response(final_events)

        # Track call count to return different responses
        call_count = 0

        def create_stream_cm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_stream_cm = MagicMock()
            if call_count == 1:
                mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response1)
            else:
                mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response2)
            mock_stream_cm.__aexit__ = AsyncMock(return_value=None)
            return mock_stream_cm

        mock_client.stream = MagicMock(side_effect=create_stream_cm)

        async with AGUIAgent(
            endpoint="http://localhost:8000/run",
            name="test-agent",
            tools=[test_calculator],
        ) as agent:
            # Verify tool is registered
            tools = await agent.tools.get_tools()
            assert len(tools) == 1
            assert tools[0].name == "test_calculator"

            # Run with tool execution
            result = await agent.run("Calculate 5 + 3")
            assert isinstance(result, ChatMessage)
            assert "8" in result.content


async def test_agui_agent_register_tool():
    """Test registering tools dynamically."""

    def my_tool(text: str) -> str:
        """Uppercase a string."""
        return text.upper()

    async with AGUIAgent(endpoint="http://localhost:8000/run", name="test-agent") as agent:
        # Register tool dynamically
        tool = agent.tools.register_tool(my_tool)
        assert tool.name == "my_tool"

        # Verify it's in the tool manager
        tools = await agent.tools.get_tools()
        assert len(tools) == 1
        assert tools[0].name == "my_tool"


async def test_agui_tool_execution_error(mock_sse_response):
    """Test handling tool execution errors."""

    def failing_tool() -> str:
        """A tool that always fails."""
        raise ValueError("Tool execution failed")

    events = [
        'data: {"type":"TOOL_CALL_START","toolCallId":"call1","toolCallName":"failing_tool"}\n\n',
        'data: {"type":"TOOL_CALL_ARGS","toolCallId":"call1","delta":"{}"}\n\n',
        'data: {"type":"TOOL_CALL_END","toolCallId":"call1"}\n\n',
    ]

    final_events = [
        'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"msg1","delta":"Tool failed"}\n\n',
    ]

    with patch("agentpool.agents.agui_agent.agui_agent.httpx2.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        mock_response1 = mock_sse_response(events)
        mock_response2 = mock_sse_response(final_events)

        call_count = 0

        def create_stream_cm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_stream_cm = MagicMock()
            if call_count == 1:
                mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response1)
            else:
                mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response2)
            mock_stream_cm.__aexit__ = AsyncMock(return_value=None)
            return mock_stream_cm

        mock_client.stream = MagicMock(side_effect=create_stream_cm)

        async with AGUIAgent(
            endpoint="http://localhost:8000/run",
            name="test-agent",
            tools=[failing_tool],
        ) as agent:
            result = await agent.run("Use the tool")
            # Should handle error and continue
            assert isinstance(result, ChatMessage)


def test_tool_call_accumulator():
    """Test ToolCallAccumulator for streaming tool arguments."""
    accumulator = ToolCallAccumulator()
    # Start a tool call
    accumulator.start("call1", "my_tool")
    # Add argument deltas
    accumulator.add_args("call1", '{"arg1":')
    accumulator.add_args("call1", ' "value1"')
    accumulator.add_args("call1", ', "arg2": 42}')
    # Complete and parse
    result = accumulator.complete("call1")
    assert result is not None
    tool_name, args = result
    assert tool_name == "my_tool"
    assert args == {"arg1": "value1", "arg2": 42}
    # Tool call should be removed after completion
    assert accumulator.get_pending("call1") is None


def test_tool_call_accumulator_invalid_json():
    """Test ToolCallAccumulator with invalid JSON."""
    accumulator = ToolCallAccumulator()
    accumulator.start("call1", "test_tool")
    accumulator.add_args("call1", "invalid json {")
    result = accumulator.complete("call1")
    assert result is not None
    tool_name, args = result
    assert tool_name == "test_tool"
    # Should wrap in raw key when parsing fails
    assert "_raw" in args


def test_tool_call_accumulator_multiple_calls():
    """Test ToolCallAccumulator with multiple concurrent tool calls."""
    accumulator = ToolCallAccumulator()
    # Start multiple tool calls
    accumulator.start("call1", "tool1")
    accumulator.start("call2", "tool2")
    # Add args to both
    accumulator.add_args("call1", '{"x": 1}')
    accumulator.add_args("call2", '{"y": 2}')
    # Check pending
    pending1 = accumulator.get_pending("call1")
    assert pending1 is not None
    assert pending1[0] == "tool1"

    pending2 = accumulator.get_pending("call2")
    assert pending2 is not None
    assert pending2[0] == "tool2"

    # Complete both
    result1 = accumulator.complete("call1")
    result2 = accumulator.complete("call2")

    assert result1 is not None
    assert result1[0] == "tool1"
    assert result1[1] == {"x": 1}

    assert result2 is not None
    assert result2[0] == "tool2"
    assert result2[1] == {"y": 2}


def test_to_agui_tool():
    """Test converting native Tool to AG-UI Tool format."""

    def example_tool(name: str, count: int = 1) -> str:
        """Process a name with count."""
        return name * count

    tool = FunctionTool.from_callable(example_tool)
    agui_tool = to_agui_tool(tool)

    assert agui_tool.name == "example_tool"
    assert "Process a name" in agui_tool.description
    assert agui_tool.parameters is not None
    assert "type" in agui_tool.parameters
    assert "properties" in agui_tool.parameters
    # Should have parameters for name and count
    assert "name" in agui_tool.parameters["properties"]
    assert "count" in agui_tool.parameters["properties"]


@pytest.mark.skip(reason="Server shutdown hangs in CI - run manually")
async def test_agui_server_and_client_e2e():
    """End-to-end test: Our AGUIServer serving an agent, AGUIAgent as client.

    This tests both our AG-UI server implementation and client together,
    verifying the full round-trip works.
    """
    # Find an available port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    # Create a simple agent with TestModel
    test_model = TestModel(custom_output_text="Hello from AG-UI server!")
    server_agent = Agent(name="test_server_agent", model=test_model)

    # Create a pool with the server agent
    async with AgentPool() as server_pool:
        server_pool[server_agent.name] = server_agent

        # Start our AG-UI server
        server = AGUIServer(server_pool, host="127.0.0.1", port=port)
        async with server, server.run_context():
            # Give server time to start
            await anyio.sleep(0.5)

            # Create AGUIAgent client directly (not from config)
            client_agent = AGUIAgent(
                endpoint=f"http://127.0.0.1:{port}/test_server_agent",
                name="remote_agent",
                timeout=10.0,
                enable_logging=False,  # Disable logging to avoid DB conflicts
            )

            async with client_agent:
                result = await client_agent.run("Hello!")

                assert isinstance(result, ChatMessage)
                assert result.content == "Hello from AG-UI server!"


@pytest.mark.skip(reason="Server shutdown hangs in CI - run manually")
async def test_agui_server_and_client_with_tools():
    """End-to-end test: AGUIServer with tools, AGUIAgent as client.

    Tests that server-side tool execution works correctly - the server
    executes its tools and the client receives the result.
    """
    # Find an available port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    # Create agent with TestModel configured to call a tool
    test_model = TestModel(call_tools=["get_info"])
    server_agent = Agent(name="tool_agent", model=test_model)

    @server_agent.tools.tool
    def get_info(topic: str) -> str:
        """Get information about a topic."""
        return f"Info about {topic}: This is test data."

    async with AgentPool() as server_pool:
        server_pool[server_agent.name] = server_agent

        server = AGUIServer(server_pool, host="127.0.0.1", port=port)
        async with server, server.run_context():
            # Give server time to start
            await anyio.sleep(0.5)

            # Create AGUIAgent client directly (not from config)
            client_agent = AGUIAgent(
                endpoint=f"http://127.0.0.1:{port}/tool_agent",
                name="remote_tool_agent",
                timeout=10.0,
                enable_logging=False,
            )

            async with client_agent:
                result = await client_agent.run("Tell me about Python")

                assert isinstance(result, ChatMessage)
                # TestModel returns tool results as JSON
                assert "get_info" in result.content
                assert "test data" in result.content.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
