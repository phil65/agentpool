"""Tasks outliving their turn are listed, stoppable and readable (``/tasks``)."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
import pytest
from slashed import CommandContext, CommandError

from agentpool import Agent, AgentPool
from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.events import TaskFinishedEvent, TaskStartedEvent, TaskUpdatedEvent
from agentpool.agents.exceptions import UnsupportedOperationError
from agentpool_config.toolsets import SubagentToolsetConfig


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import ModelMessage
    from pydantic_ai.models.function import DeltaToolCalls


def _delegating_model() -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        if len(messages) == 1:
            args = {
                "agent_or_team": "helper",
                "prompt": "work",
                "description": "Long job",
                "async_mode": True,
            }
            call: DeltaToolCalls = {
                0: DeltaToolCall(name="task", json_args=json.dumps(args), tool_call_id="call_1")
            }
            yield call
        else:
            yield "started it"

    return FunctionModel(stream_function=stream)


def _working_model(release: asyncio.Event) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "partial output"
        await release.wait()
        yield " and the rest"

    return FunctionModel(stream_function=stream)


class _Output:
    def __init__(self) -> None:
        self.lines: list[str] = []

    async def print(self, message: str) -> None:
        self.lines.append(message)


async def _command(agent: Agent[None], line: str) -> str:
    output = _Output()
    ctx = CommandContext(output=output, data=agent.get_context(), command_store=agent.command_store)
    await agent.command_store.execute_command(line, ctx)
    return "\n".join(output.lines)


async def _wait_until(condition: Any) -> None:
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def test_a_background_subagent_is_listed_read_and_stopped() -> None:
    release = asyncio.Event()
    async with AgentPool() as pool:
        await pool.add_agent(Agent(name="helper", model=_working_model(release)))
        lead = Agent(
            name="lead",
            model=_delegating_model(),
            toolsets=[SubagentToolsetConfig().get_provider()],
        )
        await pool.add_agent(lead)
        await lead.run("go")  # Its turn ends; the task goes on

        [task] = lead.running_tasks()
        assert (task.kind, task.description, task.background) == ("agent", "Long job", True)
        assert "Long job" in await _command(lead, "tasks")
        await _wait_until(lambda: lead._local_tasks)
        output = await lead.task_output(task.task_id)
        assert output.total_bytes == len(output.output.encode())

        await lead.stop_task(task.task_id)
        await _wait_until(lambda: not lead.running_tasks())  # It finished, cancelled
        assert await _command(lead, "tasks") == "No tasks running."
        with pytest.raises(KeyError, match="No running task"):
            await lead.task_output(task.task_id)


async def test_tasks_are_tracked_per_session_until_they_finish() -> None:
    agent = Agent(name="a", model="test")
    started = TaskStartedEvent(task_id="t1", kind="shell", description="build", background=True)
    await agent.emit_event(started)
    await agent.emit_event(TaskUpdatedEvent(task_id="t1", message="50%"))
    [task] = agent.running_tasks()
    assert (task.task_id, task.message) == ("t1", "50%")
    await agent.emit_event(TaskFinishedEvent(task_id="t1", status="completed"))
    assert agent.running_tasks() == []


async def test_an_agent_without_backend_tasks_says_so() -> None:
    agent = Agent(name="a", model="test")
    await agent.emit_event(TaskStartedEvent(task_id="t1", kind="shell", description="build"))
    with pytest.raises(UnsupportedOperationError):
        await agent.stop_task("t1")
    with pytest.raises(CommandError, match="cannot stop task"):
        await _command(agent, "tasks stop t1")


class _FakeCLI:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def stop_task(self, task_id: str) -> None:
        self.stopped.append(task_id)

    async def get_task_output(self, task_id: str) -> Any:
        from clawd_code_sdk.models.control import SDKControlGetTaskOutputResponse

        return SDKControlGetTaskOutputResponse(output="ok\n", total_bytes=3, truncated=False)


async def test_claude_code_stops_and_reads_tasks_through_its_cli() -> None:
    from agentpool.agents.claude_code_agent.cli_process import CliProcess
    from agentpool.agents.claude_code_agent.idle_watch import IdleWatcher

    agent = ClaudeCodeAgent(name="cc")
    cli = _FakeCLI()
    process = CliProcess("", idle_watch=IdleWatcher(agent))
    process.client = cli  # type: ignore[assignment]
    agent._processes[""] = process
    await agent.emit_event(TaskStartedEvent(task_id="b1", kind="shell", description="tests"))
    assert (await agent.task_output("b1")).output == "ok\n"
    await agent.stop_task("b1")
    assert cli.stopped == ["b1"]
