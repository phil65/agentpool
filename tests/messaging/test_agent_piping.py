from __future__ import annotations

import logging

import anyio
import pytest

from agentpool import Agent, TeamRun
from agentpool.messaging import AgentResponse
from agentpool.talk.talk import Talk


async def test_agent_piping():
    # Use explicit names for all agents
    agent1 = Agent.from_callback(lambda x: f"model: {x}", name="agent1")
    transform1 = Agent.from_callback(lambda x: f"transform1: {x}", name="transform1")
    transform2 = Agent.from_callback(lambda x: f"transform2: {x}", name="transform2")

    pipeline = TeamRun([agent1, transform1, transform2])
    result = await pipeline.execute("test")

    # Check message flow
    assert result[0].message
    assert result[1].message
    assert result[2].message
    assert result[0].message.data == "model: test"
    assert result[1].message.data == "transform1: model: test"
    assert result[2].message.data == "transform2: transform1: model: test"
    assert pipeline.execution_stats.message_count == 3


async def test_agent_piping_with_monitoring():
    def callback(text: str) -> str:
        return f"model: {text}"

    agent1 = Agent.from_callback(callback, name="agent1")

    async def transform(text: str) -> str:
        await anyio.sleep(0.1)  # Add a delay to ensure monitoring catches it
        return f"transform: {text}"

    pipeline = TeamRun([agent1, Agent.from_callback(transform)])

    # Get stats object directly from run_in_background
    stats = await pipeline.run_in_background("test")

    # Monitor progress
    updates = []
    while pipeline.is_running:
        updates.append(len(stats))  # Track number of active connections
        await anyio.sleep(0.01)

    _result = await pipeline.wait()
    assert updates  # Last update should show no active agents


async def test_agent_piping_errors(caplog):
    caplog.set_level(logging.CRITICAL)
    agent1 = Agent.from_callback(lambda x: f"model: {x}", name="agent1")
    failing = Agent.from_callback(
        lambda x: exec('raise ValueError("Transform error")'),
        name="failing_transform",
    )

    pipeline = TeamRun([agent1, failing])

    # The pipeline should break at the failing transform
    with pytest.raises(RuntimeError, match="Transform error"):
        await pipeline.execute("test")

    # Check that we can still access stats about what happened before the error
    assert len(pipeline.talk) == 1  # Only the successful connection
    assert len(pipeline.execution_stats.messages) == 1  # Only the first message
    assert pipeline.execution_stats.messages[0].content == "model: test"


async def test_agent_piping_iter(caplog):
    """Test that execute_iter allows tracking the pipeline step by step."""
    caplog.set_level(logging.CRITICAL)
    agent1 = Agent.from_callback(lambda x: f"model: {x}", name="agent1")
    failing = Agent.from_callback(
        lambda x: exec('raise ValueError("Transform error")'),
        name="failing_transform",
    )

    pipeline = TeamRun([agent1, failing])

    items = []  # Define items before the try block

    try:
        async for item in pipeline.execute_iter("test"):
            items.append(item)  # noqa: PERF401
    except RuntimeError as e:
        assert "Transform error" in str(e)  # noqa: PT017

    # We should see:
    # 1. First agent's response
    # 2. The connection object
    # Then it should fail
    assert len(items) == 2
    assert isinstance(items[0], AgentResponse)
    assert isinstance(items[1], Talk)
    assert items[0].message is not None
    assert items[0].message.content == "model: test"


async def test_agent_piping_background_error(caplog):
    """Test that background execution properly handles errors."""
    caplog.set_level(logging.CRITICAL)
    agent1 = Agent.from_callback(lambda x: f"model: {x}", name="agent1")

    def failing_transform(text: str) -> str:
        """Transformer that always fails."""
        msg = "Transform error"
        raise ValueError(msg)

    pipeline = TeamRun([agent1, Agent.from_callback(failing_transform)])
    talk = await pipeline.run_in_background("test")

    # Wait for execution to complete
    with pytest.raises(RuntimeError, match="Transform error"):
        await pipeline.wait()
    # Stats should reflect what happened before the error
    assert len(talk) == 1  # Only the successful connection
    assert len(talk.stats.messages) == 1  # Only the first message


async def test_agent_piping_async():
    async def model_callback(text: str) -> str:
        return f"model: {text}"

    agent1 = Agent.from_callback(model_callback, name="agent1")

    async def transform(text: str) -> str:
        return f"transform: {text}"

    pipeline = TeamRun([agent1, Agent.from_callback(transform)])
    result = await pipeline.execute("test")

    assert result.success
    assert len(result) == 2
    assert result[0].message
    assert result[1].message
    assert result[0].message.data == "model: test"
    assert result[1].message.data == "transform: model: test"


if __name__ == "__main__":
    pytest.main([__file__])
