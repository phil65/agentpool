"""Tests for streaming command functionality in sessions."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import anyio
import pytest
from slashed import Command

from acp import ClientCapabilities
from agentpool import Agent, AgentPool
from agentpool_server.acp_server.session import ACPSession


if TYPE_CHECKING:
    from datetime import datetime

    from acp.schema import Audience


async def test_session_command_immediate_execution():
    """Test that command execution sends updates immediately."""
    agent_pool = AgentPool()

    def simple_callback(message: str) -> str:
        return f"Response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=agent_pool)
    agent_pool.register("test_agent", agent)

    mock_client = AsyncMock()
    mock_acp_agent = AsyncMock()

    session = ACPSession(
        session_id="test_session",
        agent=agent,
        cwd="/tmp",
        client=mock_client,
        acp_agent=mock_acp_agent,
        client_capabilities=ClientCapabilities(fs=None, terminal=False),
    )

    sent_messages = []

    async def capture_message(
        message: str,
        *,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ):
        sent_messages.append(message)

    session.notifications.send_agent_text = capture_message  # type: ignore[method-assign]
    await session.execute_slash_command("/help")
    assert len(sent_messages) > 0


async def test_immediate_send_with_slow_command():
    """Test immediate sending works with commands that produce output over time."""
    agent_pool = AgentPool()

    def simple_callback(message: str) -> str:
        return f"Response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=agent_pool)
    agent_pool.register("test_agent", agent)

    mock_client = AsyncMock()
    mock_acp_agent = AsyncMock()

    session = ACPSession(
        session_id="test_session",
        agent=agent,
        cwd="/tmp",
        client=mock_client,
        acp_agent=mock_acp_agent,
        client_capabilities=ClientCapabilities(fs=None, terminal=False),
    )

    # Create a command that outputs multiple lines with delays
    async def slow_command_func(ctx, args, kwargs):
        await ctx.print("Starting task...")
        await anyio.sleep(0.01)  # Small delay
        await ctx.print("Processing...")
        await anyio.sleep(0.01)  # Small delay
        await ctx.print("Completed!")

    # Add the command to the session's command store using from_raw for raw signature
    slow_cmd = Command.from_raw(
        slow_command_func, name="slow", description="A slow command for testing"
    )
    session.command_store.register_command(slow_cmd)

    # Collect messages with timestamps to verify immediate sending
    messages_with_time = []
    start_time = time.perf_counter()

    async def capture_with_time(message):
        current_time = time.perf_counter()
        messages_with_time.append((message, current_time - start_time))

    session.notifications.send_agent_text = capture_with_time  # type: ignore[method-assign, assignment]  # ty:ignore[invalid-assignment]
    await session.execute_slash_command("/slow")
    # Verify we got multiple messages
    min_expected_messages = 3
    assert len(messages_with_time) >= min_expected_messages
    # Verify messages came at different times (immediate sending behavior)
    times = [time for _, time in messages_with_time]
    assert times[1] > times[0]  # Second message came after first
    assert times[2] > times[1]  # Third message came after second

    # Verify message content is correct
    expected_messages = ["Starting task...", "Processing...", "Completed!"]
    actual_messages = [message for message, _ in messages_with_time]
    for expected in expected_messages:
        assert expected in actual_messages


async def test_immediate_send_error_handling(caplog: pytest.LogCaptureFixture):
    """Test that errors in commands are properly sent immediately."""
    caplog.set_level("CRITICAL")  # Suppress expected error logs
    agent_pool = AgentPool()

    def simple_callback(message: str) -> str:
        return f"Response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=agent_pool)
    agent_pool.register("test_agent", agent)
    mock_client = AsyncMock()

    # Track created tasks to wait for them
    created_tasks = []

    def mock_create_task(coro, *, name=None):
        task = asyncio.create_task(coro, name=name)
        created_tasks.append(task)
        return task

    mock_acp_agent = AsyncMock()
    mock_acp_agent.tasks.create_task = mock_create_task

    session = ACPSession(
        session_id="test_session",
        agent=agent,
        cwd="/tmp",
        client=mock_client,
        acp_agent=mock_acp_agent,
        client_capabilities=ClientCapabilities(fs=None, terminal=False),
    )

    async def failing_command(ctx, args, kwargs):
        await ctx.print("Starting...")
        msg = "Command failed!"
        raise ValueError(msg)

    fail_cmd = Command.from_raw(failing_command, name="fail", description="A failing command")
    session.command_store.register_command(fail_cmd)

    # Collect all messages
    sent_messages = []

    async def capture_message(
        message: str,
        *,
        audience: Audience | None = None,
        last_modified: datetime | str | None = None,
        priority: float | None = None,
    ):
        sent_messages.append(message)

    session.notifications.send_agent_text = capture_message  # type: ignore[method-assign]
    # Execute failing command
    await session.execute_slash_command("/fail")

    # Wait for any async error notification tasks to complete
    if created_tasks:
        await asyncio.gather(*created_tasks, return_exceptions=True)

    # Should get the initial output plus error message
    min_expected_messages = 2
    assert len(sent_messages) >= min_expected_messages

    # Check that we got both normal output and error
    message_text = " ".join(sent_messages)
    assert "Starting..." in message_text
    assert "Command error:" in message_text


if __name__ == "__main__":
    pytest.main(["-v", __file__])
