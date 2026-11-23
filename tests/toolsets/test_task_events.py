"""The task tool reports subagent runs as task events."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from agentpool import Agent, AgentPool
from agentpool.agents.events import StreamCompleteEvent, TaskFinishedEvent, TaskStartedEvent
from agentpool_config.toolsets import SubagentToolsetConfig
from agentpool_toolsets.builtin import subagent_tools


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage
    from pydantic_ai.models.function import DeltaToolCalls

    from agentpool.agents.base_agent import BaseAgent


def _lead_model(*, async_mode: bool) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if len(messages) == 1:  # First request of the first run: delegate
            args = {
                "agent_or_team": "helper",
                "prompt": "say hello",
                "description": "Greet",
                "async_mode": async_mode,
            }
            call: DeltaToolCalls = {
                0: DeltaToolCall(name="task", json_args=json.dumps(args), tool_call_id="call_task")
            }
            yield call
        else:
            yield "done"

    return FunctionModel(stream_function=stream)


async def _run(*, async_mode: bool) -> list[Any]:
    async with AgentPool() as pool:
        helper = Agent(name="helper", model=TestModel(custom_output_text="hello"))
        await pool.add_agent(helper)
        lead = Agent(
            name="lead",
            model=_lead_model(async_mode=async_mode),
            toolsets=[SubagentToolsetConfig().get_provider()],
        )
        await pool.add_agent(lead)
        background_events: list[Any] = []

        async def on_background(background: BaseAgent.BackgroundEvent) -> None:
            background_events.append(background.event)

        # A background task ending after the run is reported as a background event (which
        # may come before the streamed events are all collected: keep them apart)
        lead.background_event.connect(on_background)
        streamed = [e async for e in lead.run_stream("go")]
        while subagent_tools._background_tasks:
            await asyncio.sleep(0.01)
        events = [*streamed, *background_events]
        return [e for e in events if isinstance(e, TaskStartedEvent | TaskFinishedEvent)]


async def test_background_task_ending_after_the_run_is_a_background_event() -> None:
    """The finish of a task that outlives its run is not held back for the next run."""
    async with AgentPool() as pool:
        lead = Agent(name="lead", model=TestModel(custom_output_text="ok"))
        await pool.add_agent(lead)
        received: list[Any] = []

        async def on_background(background: BaseAgent.BackgroundEvent) -> None:
            received.append(background)

        lead.background_event.connect(on_background)
        _ = [e async for e in lead.run_stream("go")]
        finished = TaskFinishedEvent(task_id="t1", status="completed")
        await lead.emit_event(finished)
        (background,) = received
        assert (background.agent_name, background.event) == ("lead", finished)
        assert background.session_id == lead.session_id
        assert lead._event_queue.empty()


async def test_event_queued_as_the_run_ends_is_a_background_event() -> None:
    """An event queued after the stream stopped draining is not stranded in the queue."""
    async with AgentPool() as pool:
        lead = Agent(name="lead", model=TestModel(custom_output_text="ok"))
        await pool.add_agent(lead)
        received: list[Any] = []

        async def on_background(background: BaseAgent.BackgroundEvent) -> None:
            received.append(background.event)

        lead.background_event.connect(on_background)
        finished = TaskFinishedEvent(task_id="t1", status="completed")
        async for event in lead.run_stream("go"):
            if isinstance(event, StreamCompleteEvent):  # the run is active, nothing drains
                lead._event_queue.put_nowait(finished)
        assert received == [finished]
        assert lead._event_queue.empty()


async def test_sync_task_events() -> None:
    started, finished = await _run(async_mode=False)
    assert started == TaskStartedEvent(
        task_id="call_task",
        kind="agent",
        description="Greet",
        tool_call_id="call_task",
        agent_type="helper",
    )
    assert finished == TaskFinishedEvent(task_id="call_task", status="completed")


async def test_background_task_events() -> None:
    started, finished = await _run(async_mode=True)
    assert isinstance(started, TaskStartedEvent)
    assert started.background
    assert isinstance(finished, TaskFinishedEvent)
    assert (finished.task_id, finished.status) == (started.task_id, "completed")
    assert finished.output_ref == f"/tasks/{started.task_id}/output.md"
    assert finished.summary == "hello"
