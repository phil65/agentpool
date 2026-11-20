"""Step lifecycle events (one per model request)."""

from __future__ import annotations

from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.events import StepCompletedEvent, StepStartedEvent


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


async def test_native_agent_emits_one_step_per_model_request() -> None:
    async with Agent(name="a", model=TestModel(call_tools=["add"]), tools=[add]) as agent:
        events = [e async for e in agent.run_stream("hi")]
    starts = [e for e in events if isinstance(e, StepStartedEvent)]
    completions = [e for e in events if isinstance(e, StepCompletedEvent)]
    assert len(starts) == 2  # tool call request + final answer request
    assert [c.step_id for c in completions] == [s.step_id for s in starts]
    assert all(c.usage.input_tokens > 0 for c in completions)
    assert completions[-1].usage.output_tokens > 0
    tool_index = next(
        i for i, e in enumerate(events) if type(e).__name__ == "ToolCallCompleteEvent"
    )
    # Tool execution happens between the two requests.
    assert events.index(completions[0]) < tool_index < events.index(starts[1])
