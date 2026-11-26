"""Canonical tool lifecycle events (ToolCallStartEvent -> ToolCallCompleteEvent)."""

from collections import Counter

from pydantic_ai import ModelRetry
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.events import ToolCallCompleteEvent, ToolCallStartEvent


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


async def test_one_start_and_one_completion_per_call() -> None:
    async with Agent(name="a", model=TestModel(call_tools=["add"]), tools=[add]) as agent:
        events = [e async for e in agent.run_stream("hi")]
    starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
    completions = [e for e in events if isinstance(e, ToolCallCompleteEvent)]
    assert [s.tool_name for s in starts] == ["add"]
    assert [c.tool_call_id for c in completions] == [starts[0].tool_call_id]
    assert completions[0].error is None
    assert events.index(starts[0]) < events.index(completions[0])


async def test_retried_call_completes_with_error() -> None:
    attempts = Counter[str]()

    def flaky(x: int) -> str:
        """Fails on the first attempt."""
        attempts["flaky"] += 1
        if attempts["flaky"] == 1:
            raise ModelRetry("try again")
        return "ok"

    async with Agent(name="a", model=TestModel(call_tools=["flaky"]), tools=[flaky]) as agent:
        events = [e async for e in agent.run_stream("hi")]
    completions = [e for e in events if isinstance(e, ToolCallCompleteEvent)]
    assert completions[0].error is not None
    assert "try again" in completions[0].error
    assert completions[-1].error is None
    per_call = Counter(c.tool_call_id for c in completions)
    assert all(count == 1 for count in per_call.values())
