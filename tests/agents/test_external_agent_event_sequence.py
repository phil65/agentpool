"""Integration tests for event sequence consistency across agent types.

These tests verify that all agent types (Agent, ACPAgent, ClaudeCodeAgent, CodexAgent)
emit events in a consistent sequence when executing the same logical flow:
text -> tool call -> text.

The tests use two collection methods:
1. Manual iteration over run_stream()
2. Event handler callback (passed via event_handlers parameter to run_stream)

Both should capture the same events, ensuring event handlers receive everything.

Run with: pytest tests/agents/test_external_agent_event_sequence.py -v -m integration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from pydantic_ai import RunContext  # noqa: TC002
import pytest

from agentpool import Agent
from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.codex_agent import CodexAgent
from agentpool.agents.events import StreamCompleteEvent, ToolCallCompleteEvent


# Mark all tests in this module as integration tests
pytestmark = [pytest.mark.integration, pytest.mark.slow]


# --- Test Tool ---


def echo_tool(ctx: RunContext[None], message: str) -> str:
    """Echo the message back.

    Args:
        ctx: Run context
        message: Message to echo

    Returns:
        The echoed message
    """
    return f"Echo: {message}"


# --- Event Collection ---


@dataclass
class EventCollector:
    """Collects events from both iteration and event handler."""

    iterated_events: list[Any] = field(default_factory=list)
    handler_events: list[Any] = field(default_factory=list)

    async def handle_event(self, ctx: Any, event: Any) -> None:
        """Event handler callback."""
        self.handler_events.append(event)

    def get_iterated_types(self) -> list[str]:
        """Get event type names from iteration."""
        return [type(e).__name__ for e in self.iterated_events]

    def get_handler_types(self) -> list[str]:
        """Get event type names from handler."""
        return [type(e).__name__ for e in self.handler_events]


def normalize_event_sequence(events: list[Any]) -> list[str]:
    """Extract event type sequence, collapsing consecutive duplicates.

    This normalizes different text lengths (varying PartDeltaEvent counts)
    into a comparable sequence.
    """
    result: list[str] = []
    for event in events:
        event_type = type(event).__name__
        # Collapse consecutive same-type events
        if not result or result[-1] != event_type:
            result.append(event_type)
    return result


def extract_key_events(events: list[Any]) -> list[str]:
    """Extract only structurally significant events for comparison."""
    key_types = {
        "RunStartedEvent",
        "ToolCallStartEvent",
        "ToolCallCompleteEvent",
        "StreamCompleteEvent",
    }
    return [type(e).__name__ for e in events if type(e).__name__ in key_types]


# --- Test Prompt ---

TOOL_CALL_PROMPT = """\
Follow these instructions exactly:
1. First say "Hello, I will now use a tool."
2. Then call the echo_tool with message "test message"
3. After the tool result, say "The tool has been called. Goodbye."
"""


# --- Agent Fixtures ---


@pytest.fixture
def acp_agent_config_with_tool(tmp_path: Path) -> tuple[Any, Path]:
    """Create ACPAgent config with echo tool via config file."""
    from agentpool.models.acp_agents import ACPAgentConfig

    config_yaml = """
agents:
  test_agent:
    type: native
    model: openai:gpt-4o-mini
    tools:
      - tests.agents.test_external_agent_event_sequence:echo_tool
"""
    config_file = tmp_path / "test_config.yml"
    config_file.write_text(config_yaml)

    config = ACPAgentConfig(
        command="uv",
        args=[
            "run",
            "agentpool",
            "serve-acp",
            str(config_file),
            "--agent",
            "test_agent",
        ],
        name="acp-test-agent",
        cwd=str(Path.cwd()),
        # `uv run agentpool` starts a console script: the repo root (with `tests`, where
        # the tool lives) is not on its import path otherwise
        env_vars={"PYTHONPATH": str(Path.cwd())},
    )
    return config, config_file


@pytest.fixture
def claude_code_agent_config() -> dict[str, Any]:
    """Create ClaudeCodeAgent config kwargs."""
    return {
        "name": "claude-code-test-agent",
        "model": "claude-haiku-5-5",
        "env": str(Path.cwd()),
        "allowed_tools": ["Bash"],
        "permission_mode": "bypassPermissions",
    }


@pytest.fixture
def codex_agent_config() -> dict[str, Any]:
    """Create CodexAgent config kwargs."""
    return {
        "name": "codex-test-agent",
        "env": str(Path.cwd()),  # The account's default model
        "reasoning_effort": "medium",
    }


@pytest.fixture(params=["claude_code", "codex"])
def external_agent_config(request: pytest.FixtureRequest) -> tuple[type, dict[str, Any]]:
    """Parametrized fixture for external agent configs."""
    if request.param == "claude_code":
        return ClaudeCodeAgent, request.getfixturevalue("claude_code_agent_config")
    return CodexAgent, request.getfixturevalue("codex_agent_config")


# --- Tests ---


async def test_native_agent_event_sequence():
    """Test native Agent emits events in expected sequence."""
    collector = EventCollector()

    agent = Agent(
        name="native-test-agent",
        model="openai:gpt-4o-mini",
        tools=[echo_tool],
    )

    async with agent:
        with anyio.fail_after(30.0):
            async for event in agent.run_stream(
                TOOL_CALL_PROMPT, event_handlers=[collector.handle_event]
            ):
                collector.iterated_events.append(event)

    # Verify both collection methods got the same events
    iterated_types = collector.get_iterated_types()
    handler_types = collector.get_handler_types()
    assert iterated_types == handler_types, "Handler should receive same events as iteration"

    # Verify key event sequence
    key_events = extract_key_events(collector.iterated_events)
    assert key_events[0] == "RunStartedEvent", "Must start with RunStartedEvent"
    assert key_events[-1] == "StreamCompleteEvent", "Must end with StreamCompleteEvent"

    # Tool call sequence: ToolCallCompleteEvent should be present if tool was called
    if "ToolCallStartEvent" in key_events or "ToolCallCompleteEvent" in key_events:
        assert "ToolCallCompleteEvent" in key_events, "Tool call should emit ToolCallCompleteEvent"

    # Verify StreamCompleteEvent has valid message
    complete_events = [e for e in collector.iterated_events if isinstance(e, StreamCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].message.role == "assistant"
    assert complete_events[0].message.content


async def test_acp_agent_event_sequence(acp_agent_config_with_tool: tuple[Any, Path]):
    """Test ACPAgent emits events in expected sequence."""
    from agentpool.agents.acp_agent import ACPAgent

    config, _ = acp_agent_config_with_tool
    collector = EventCollector()

    async with ACPAgent.from_config(config) as agent:
        with anyio.fail_after(45.0):
            async for event in agent.run_stream(
                TOOL_CALL_PROMPT, event_handlers=[collector.handle_event]
            ):
                collector.iterated_events.append(event)

    # Verify both collection methods got the same events
    iterated_types = collector.get_iterated_types()
    handler_types = collector.get_handler_types()
    assert iterated_types == handler_types, "Handler should receive same events as iteration"

    # Verify key event sequence
    key_events = extract_key_events(collector.iterated_events)
    assert key_events[0] == "RunStartedEvent", "Must start with RunStartedEvent"
    assert key_events[-1] == "StreamCompleteEvent", "Must end with StreamCompleteEvent"


async def test_claude_code_agent_event_sequence(claude_code_agent_config: dict[str, Any]):
    """Test ClaudeCodeAgent emits events in expected sequence."""
    collector = EventCollector()

    prompt = """\
Follow these steps exactly:
1. Say "I will run a command"
2. Run: echo "hello from bash"
3. Say "Done"
"""

    try:
        async with ClaudeCodeAgent(**claude_code_agent_config) as agent:
            with anyio.fail_after(60.0):
                async for event in agent.run_stream(
                    prompt, event_handlers=[collector.handle_event]
                ):
                    collector.iterated_events.append(event)
    except TimeoutError:
        pytest.skip("Claude Code agent took too long to respond")

    # Verify both collection methods got the same events
    iterated_types = collector.get_iterated_types()
    handler_types = collector.get_handler_types()
    assert iterated_types == handler_types, "Handler should receive same events as iteration"

    # Verify key event sequence
    key_events = extract_key_events(collector.iterated_events)
    assert key_events[0] == "RunStartedEvent", "Must start with RunStartedEvent"
    assert key_events[-1] == "StreamCompleteEvent", "Must end with StreamCompleteEvent"


async def test_codex_agent_event_sequence(codex_agent_config: dict[str, Any]):
    """Test CodexAgent emits events in expected sequence."""
    collector = EventCollector()

    prompt = """\
Follow these steps exactly:
1. Say "I will run a command"
2. Run: echo "hello from codex"
3. Say "Done"
"""

    try:
        async with CodexAgent(**codex_agent_config) as agent:
            with anyio.fail_after(60.0):
                async for event in agent.run_stream(
                    prompt, event_handlers=[collector.handle_event]
                ):
                    collector.iterated_events.append(event)
    except TimeoutError:
        pytest.skip("Codex agent took too long to respond")

    # Verify both collection methods got the same events
    iterated_types = collector.get_iterated_types()
    handler_types = collector.get_handler_types()
    assert iterated_types == handler_types, "Handler should receive same events as iteration"

    # Verify key event sequence
    key_events = extract_key_events(collector.iterated_events)
    assert key_events[0] == "RunStartedEvent", "Must start with RunStartedEvent"
    assert key_events[-1] == "StreamCompleteEvent", "Must end with StreamCompleteEvent"


async def test_external_agent_event_consistency(
    external_agent_config: tuple[type, dict[str, Any]],
):
    """Parametrized test for external agents (ClaudeCode and Codex) event sequence."""
    agent_class, config = external_agent_config
    collector = EventCollector()

    prompt = """\
Follow these steps exactly:
1. Say "Starting task"
2. Say "Task complete"
"""

    try:
        async with agent_class(**config) as agent:
            with anyio.fail_after(60.0):
                async for event in agent.run_stream(
                    prompt, event_handlers=[collector.handle_event]
                ):
                    collector.iterated_events.append(event)
    except TimeoutError:
        pytest.skip(f"{agent_class.__name__} took too long to respond")

    # Verify both collection methods got the same events
    iterated_types = collector.get_iterated_types()
    handler_types = collector.get_handler_types()
    assert iterated_types == handler_types, "Handler should receive same events as iteration"

    # Verify key event sequence
    key_events = extract_key_events(collector.iterated_events)
    assert key_events[0] == "RunStartedEvent", "Must start with RunStartedEvent"
    assert key_events[-1] == "StreamCompleteEvent", "Must end with StreamCompleteEvent"


async def test_event_sequence_consistency_across_agents(
    acp_agent_config_with_tool: tuple[Any, Path],
):
    """Test that native Agent and ACPAgent emit consistent key event sequences."""
    from agentpool.agents.acp_agent import ACPAgent

    # Collect from native agent
    native_collector = EventCollector()
    native_agent = Agent(
        name="native-test-agent",
        model="openai:gpt-4o-mini",
        tools=[echo_tool],
    )

    async with native_agent:
        with anyio.fail_after(30.0):
            async for event in native_agent.run_stream(
                TOOL_CALL_PROMPT, event_handlers=[native_collector.handle_event]
            ):
                native_collector.iterated_events.append(event)

    native_key_events = extract_key_events(native_collector.iterated_events)

    # Collect from ACP agent
    acp_collector = EventCollector()
    config, _ = acp_agent_config_with_tool
    try:
        async with ACPAgent.from_config(config) as agent:
            with anyio.fail_after(45.0):
                async for event in agent.run_stream(
                    TOOL_CALL_PROMPT, event_handlers=[acp_collector.handle_event]
                ):
                    acp_collector.iterated_events.append(event)
    except TimeoutError:
        pytest.skip("ACP server took too long to respond")

    acp_key_events = extract_key_events(acp_collector.iterated_events)

    # Both should have same structure
    assert native_key_events[0] == acp_key_events[0] == "RunStartedEvent"
    assert native_key_events[-1] == acp_key_events[-1] == "StreamCompleteEvent"

    # Both should have tool call events (order may vary slightly)
    native_has_tool = "ToolCallCompleteEvent" in native_key_events
    acp_has_tool = "ToolCallCompleteEvent" in acp_key_events

    # If both have tool calls, verify ordering
    if native_has_tool and acp_has_tool:
        native_tool_idx = native_key_events.index("ToolCallCompleteEvent")
        native_complete_idx = native_key_events.index("StreamCompleteEvent")
        assert native_tool_idx < native_complete_idx

        acp_tool_idx = acp_key_events.index("ToolCallCompleteEvent")
        acp_complete_idx = acp_key_events.index("StreamCompleteEvent")
        assert acp_tool_idx < acp_complete_idx


async def test_handler_receives_all_events():
    """Verify event handler receives every event that iteration yields."""
    collector = EventCollector()

    agent = Agent(
        name="native-test-agent",
        model="openai:gpt-4o-mini",
    )

    async with agent:
        with anyio.fail_after(30.0):
            async for event in agent.run_stream(
                "Just say hello", event_handlers=[collector.handle_event]
            ):
                collector.iterated_events.append(event)

    # Handler should have received exactly the same events
    assert len(collector.handler_events) == len(collector.iterated_events)

    for i, (handler_event, iter_event) in enumerate(
        zip(collector.handler_events, collector.iterated_events, strict=True)
    ):
        assert type(handler_event) is type(iter_event), f"Event {i} type mismatch"


async def test_stream_complete_event_structure():
    """Verify StreamCompleteEvent has required fields across all agents."""
    collector = EventCollector()

    agent = Agent(
        name="native-test-agent",
        model="openai:gpt-4o-mini",
    )

    async with agent:
        with anyio.fail_after(30.0):
            async for event in agent.run_stream(
                "Say hello", event_handlers=[collector.handle_event]
            ):
                collector.iterated_events.append(event)

    complete_events = [e for e in collector.iterated_events if isinstance(e, StreamCompleteEvent)]
    assert len(complete_events) == 1

    complete = complete_events[0]
    msg = complete.message

    # Required fields
    assert msg.role == "assistant"
    assert msg.content is not None
    assert msg.message_id is not None
    assert msg.session_id is not None
    assert msg.name is not None


async def test_tool_call_complete_event_structure():
    """Verify ToolCallCompleteEvent has required fields."""
    collector = EventCollector()

    agent = Agent(
        name="native-test-agent",
        model="openai:gpt-4o-mini",
        tools=[echo_tool],
    )

    async with agent:
        with anyio.fail_after(30.0):
            async for event in agent.run_stream(
                TOOL_CALL_PROMPT, event_handlers=[collector.handle_event]
            ):
                collector.iterated_events.append(event)

    tool_complete_events = [
        e for e in collector.iterated_events if isinstance(e, ToolCallCompleteEvent)
    ]

    # Model might not always call the tool, but if it does, verify structure
    for event in tool_complete_events:
        assert event.tool_name is not None
        assert event.tool_call_id is not None
        assert event.tool_input is not None
        assert event.tool_result is not None
        assert event.agent_name is not None


if __name__ == "__main__":
    pytest.main(["-v", "-m", "integration", __file__])
