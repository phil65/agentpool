"""End-to-end tests for execution environment tools."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from anyenv.process_manager import ProcessOutput
from exxec.mock_provider import MockExecutionEnvironment
from exxec.models import ExecutionResult
import pytest

from agentpool import Agent, AgentContext
from agentpool.agents.events import ToolCallProgressEvent
from agentpool.tool_impls.bash import BashTool
from agentpool.tool_impls.execute_code import ExecuteCodeTool
from agentpool_toolsets.builtin.execution_environment import ProcessManagementTools


if TYPE_CHECKING:
    from pathlib import Path

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.events import RichAgentStreamEvent


_captured: dict[int, list[RichAgentStreamEvent[Any]]] = {}
"""Events each test agent emitted (tools run outside a run, so they arrive as background events)."""


def drain_event_queue(agent: Agent) -> list[RichAgentStreamEvent[Any]]:
    """Take the events the agent emitted so far."""
    events = _captured.get(id(agent), [])
    _captured[id(agent)] = []
    return events


def get_progress_events(agent: Agent) -> list[ToolCallProgressEvent]:
    """Get all ToolCallProgressEvent from the agent's queue."""
    events = drain_event_queue(agent)
    return [e for e in events if isinstance(e, ToolCallProgressEvent)]


def extract_process_id(result: str) -> str:
    """Extract process ID from a result string like 'Started background process mock_abc123'."""
    match = re.search(r"mock_[a-f0-9]+", result)
    if match:
        return match.group(0)
    msg = f"Could not extract process ID from: {result}"
    raise ValueError(msg)


@pytest.fixture
def test_agent() -> Agent[None]:
    """Create a minimal agent for testing event emission, capturing what it emits."""
    agent = Agent[None](name="test_agent", model="test")
    _captured[id(agent)] = []

    async def capture(background: BaseAgent.BackgroundEvent) -> None:
        _captured[id(agent)].append(background.event)

    agent.background_event.connect(capture)
    return agent


@pytest.fixture
def agent_ctx(test_agent: Agent[None]) -> AgentContext:
    """Create a real AgentContext for testing."""
    return AgentContext(
        node=test_agent,
        tool_call_id="test_call_123",
        tool_name="test_tool",
        tool_input={"command": "echo", "args": ["hello"]},
    )


class TestToolsInitialization:
    """Test tools initialization and configuration."""

    def test_custom_env_is_used(self):
        """Test that custom environment is properly set."""
        env = MockExecutionEnvironment()
        tools = ProcessManagementTools(env=env)
        assert tools._env is env

    def test_default_env_is_created(self):
        """Test that ProcessManagementTools creates default LocalExecutionEnvironment."""
        tools = ProcessManagementTools()
        assert tools._env is None  # None means fallback to agent's env


class TestCodeExecution:
    """Test code execution tools."""

    async def test_execute_code_success(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test successful code execution."""
        env = MockExecutionEnvironment(
            code_results={
                "print(42)": ExecutionResult(
                    result=None,
                    duration=0.1,
                    success=True,
                    stdout="42\n",
                    stderr="",
                    exit_code=0,
                ),
            },
        )
        tool = ExecuteCodeTool(name="execute_code", env=env)

        result = await tool.execute_and_unwrap(ctx=agent_ctx, code="print(42)", title="test print")
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "42" in result

        # Check events were emitted
        events = get_progress_events(test_agent)
        assert len(events) >= 1

    async def test_execute_code_failure(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test code execution failure."""
        env = MockExecutionEnvironment(
            code_results={
                "print(x)": ExecutionResult(
                    result=None,
                    duration=0.1,
                    success=False,
                    stdout="",
                    stderr="",
                    error="NameError: name 'x' is not defined",
                    error_type="NameError",
                    exit_code=1,
                ),
            },
        )
        tool = ExecuteCodeTool(name="execute_code", env=env)

        result = await tool.execute_and_unwrap(
            ctx=agent_ctx, code="print(x)", title="test undefined"
        )
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "NameError" in result

        # Check events were emitted
        events = get_progress_events(test_agent)
        assert len(events) >= 1

    async def test_execute_code_exception(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test code execution with exception."""
        env = MockExecutionEnvironment(
            code_exceptions={
                "bad code": RuntimeError("Execution failed"),
            },
        )
        tool = ExecuteCodeTool(name="execute_code", env=env)

        result = await tool.execute_and_unwrap(
            ctx=agent_ctx, code="bad code", title="test exception"
        )
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "Execution failed" in result

    async def test_execute_command_success(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test successful command execution."""
        env = MockExecutionEnvironment(
            command_results={
                "echo hello world": ExecutionResult(
                    result=None,
                    duration=0.2,
                    success=True,
                    stdout="hello world\n",
                    stderr="",
                    exit_code=0,
                ),
            },
        )
        tool = BashTool(name="bash", env=env)

        result = await tool.execute_and_unwrap(ctx=agent_ctx, command="echo hello world")
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "hello world" in result

        # Check events were emitted
        events = get_progress_events(test_agent)
        assert len(events) >= 1

    async def test_execute_command_with_output_limit(
        self, agent_ctx: AgentContext, test_agent: Agent
    ):
        """Test command execution with output truncation."""
        long_output = "x" * 1000
        env = MockExecutionEnvironment(
            command_results={
                "echo": ExecutionResult(
                    result=None,
                    duration=0.1,
                    success=True,
                    stdout=long_output,
                    stderr="",
                    exit_code=0,
                ),
            },
        )
        tool = BashTool(name="bash", env=env)

        result = await tool.execute_and_unwrap(ctx=agent_ctx, command="echo", output_limit=100)
        # Tools now return formatted strings
        assert isinstance(result, str)
        # Output should be truncated
        assert len(result) < len(long_output) or "truncated" in result.lower()


class TestProcessLifecycle:
    """Test process lifecycle scenarios."""

    async def test_start_process_success(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test successful process start."""
        env = MockExecutionEnvironment()
        tools = ProcessManagementTools(env=env)

        result = await tools.start_process(
            agent_ctx,
            command="echo",
            args=["hello", "world"],
            cwd="/tmp",
        )
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "Started background process" in result
        assert "mock_" in result
        assert "echo" in result

        # Check event was emitted
        events = get_progress_events(test_agent)
        assert len(events) == 1
        assert "Running: echo" in str(events[0].title)

    async def test_start_process_failure(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test process start failure."""
        env = MockExecutionEnvironment()
        # Inject a failure into the mock process manager

        async def failing_start(
            command: str,
            args: list[str] | None = None,
            cwd: str | Path | None = None,
            env: dict[str, str] | None = None,
            output_limit: int | None = None,
        ) -> str:
            raise FileNotFoundError("Command not found")

        env.process_manager.start_process = failing_start  # type: ignore[method-assign]
        tools = ProcessManagementTools(env=env)

        result = await tools.start_process(agent_ctx, command="nonexistent")
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "Command not found" in result or "Failed" in result

        # Check event was emitted with failure
        events = get_progress_events(test_agent)
        assert len(events) == 1

    async def test_get_process_output_running(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test getting output from running process."""
        env = MockExecutionEnvironment(
            default_process_output=ProcessOutput(
                stdout="output line 1\noutput line 2\n",
                stderr="",
                combined="output line 1\noutput line 2\n",
                exit_code=None,
            ),
        )
        tools = ProcessManagementTools(env=env)

        # Start a process first
        start_result = await tools.start_process(agent_ctx, command="sleep", args=["10"])
        process_id = extract_process_id(start_result)
        drain_event_queue(test_agent)  # Clear start event

        result = await tools.get_process_output(agent_ctx, process_id)
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "output line 1" in result

        # Check event was emitted
        events = get_progress_events(test_agent)
        assert len(events) == 1

    async def test_get_process_output_completed(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test getting output from completed process."""
        env = MockExecutionEnvironment(
            default_process_output=ProcessOutput(
                stdout="done\n",
                stderr="",
                combined="done\n",
                exit_code=0,
            ),
        )
        tools = ProcessManagementTools(env=env)

        # Start a process first
        start_result = await tools.start_process(agent_ctx, command="echo", args=["done"])
        process_id = extract_process_id(start_result)

        result = await tools.get_process_output(agent_ctx, process_id)
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "done" in result
        assert "completed" in result.lower() or "exit" in result.lower()

    async def test_wait_for_process_success(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test waiting for process completion."""
        env = MockExecutionEnvironment(
            default_process_output=ProcessOutput(
                stdout="Process completed\n",
                stderr="",
                combined="Process completed\n",
                exit_code=0,
            ),
        )
        tools = ProcessManagementTools(env=env)

        # Start a process first
        start_result = await tools.start_process(agent_ctx, command="echo", args=["done"])
        process_id = extract_process_id(start_result)
        drain_event_queue(test_agent)  # Clear start event

        result = await tools.wait_for_process(agent_ctx, process_id)
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "Process completed" in result

        # Check event was emitted
        events = get_progress_events(test_agent)
        assert len(events) == 1
        assert events[0].process is not None
        assert events[0].process.exit_code == 0

    async def test_wait_for_process_failure(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test waiting for failed process."""
        env = MockExecutionEnvironment(
            default_process_output=ProcessOutput(
                stdout="",
                stderr="Command failed\n",
                combined="Command failed\n",
                exit_code=1,
            ),
        )
        tools = ProcessManagementTools(env=env)

        # Start a process first
        start_result = await tools.start_process(agent_ctx, command="false")
        process_id = extract_process_id(start_result)

        result = await tools.wait_for_process(agent_ctx, process_id)
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "Command failed" in result

    async def test_kill_process_success(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test killing a process."""
        env = MockExecutionEnvironment()
        tools = ProcessManagementTools(env=env)

        # Start a process first
        start_result = await tools.start_process(agent_ctx, command="sleep", args=["100"])
        process_id = extract_process_id(start_result)
        drain_event_queue(test_agent)  # Clear start event

        result = await tools.kill_process(agent_ctx, process_id)
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert process_id in result
        assert "terminated" in result.lower()

        # Check event was emitted
        events = get_progress_events(test_agent)
        assert len(events) == 1
        assert "Killed process" in str(events[0].title)

    async def test_kill_process_not_found(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test killing nonexistent process."""
        env = MockExecutionEnvironment()
        tools = ProcessManagementTools(env=env)

        result = await tools.kill_process(agent_ctx, "invalid")
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "Error" in result or "not found" in result.lower()

        # Check event was emitted with failure
        events = get_progress_events(test_agent)
        assert len(events) == 1

    async def test_release_process_success(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test releasing process resources."""
        env = MockExecutionEnvironment()
        tools = ProcessManagementTools(env=env)

        # Start a process first
        start_result = await tools.start_process(agent_ctx, command="echo")
        process_id = extract_process_id(start_result)
        drain_event_queue(test_agent)  # Clear start event

        result = await tools.release_process(agent_ctx, process_id)
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert process_id in result
        assert "released" in result.lower()

        # Check event was emitted
        events = get_progress_events(test_agent)
        assert len(events) == 1
        assert "Released process" in str(events[0].title)

    async def test_list_processes_empty(self, agent_ctx: AgentContext):
        """Test listing when no processes running."""
        env = MockExecutionEnvironment()
        tools = ProcessManagementTools(env=env)

        result = await tools.list_processes(agent_ctx)
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "0" in result or "No" in result or "empty" in result.lower()

    async def test_list_processes_with_results(self, agent_ctx: AgentContext):
        """Test listing active processes."""
        env = MockExecutionEnvironment()
        tools = ProcessManagementTools(env=env)

        # Start two processes
        await tools.start_process(agent_ctx, command="sleep", args=["60"], cwd="/tmp")
        await tools.start_process(agent_ctx, command="echo", args=["done"], cwd="/home")

        result = await tools.list_processes(agent_ctx)
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "2" in result or "Active processes" in result
        # Both process IDs should be in the output
        assert "mock_" in result

    async def test_list_processes_partial_failure(self, agent_ctx: AgentContext):
        """Test listing when some process info fails."""
        env = MockExecutionEnvironment()
        tools = ProcessManagementTools(env=env)

        # Start a process
        await tools.start_process(agent_ctx, command="sleep", args=[])

        # Override get_process_info to fail for the second call
        original_get_info = env.process_manager.get_process_info
        call_count = 0

        async def failing_get_info(process_id: str) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("Can't get info")
            return await original_get_info(process_id)

        # Start another process, then make info fail
        await tools.start_process(agent_ctx, command="echo", args=[])
        env.process_manager.get_process_info = failing_get_info  # type: ignore[method-assign]

        result = await tools.list_processes(agent_ctx)
        # Tools now return formatted strings
        assert isinstance(result, str)
        # Should still list processes even with partial failure
        assert "mock_" in result


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    async def test_truncated_output(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test handling of truncated output."""
        env = MockExecutionEnvironment(
            default_process_output=ProcessOutput(
                stdout="partial output...",
                stderr="",
                combined="partial output...",
                truncated=True,
                exit_code=None,
            ),
        )
        tools = ProcessManagementTools(env=env)

        # Start a process first
        start_result = await tools.start_process(agent_ctx, command="big_command")
        process_id = extract_process_id(start_result)

        result = await tools.get_process_output(agent_ctx, process_id)
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "partial output" in result

    async def test_process_with_signal(self, agent_ctx: AgentContext, test_agent: Agent):
        """Test process terminated by signal."""
        env = MockExecutionEnvironment(
            default_process_output=ProcessOutput(
                stdout="",
                stderr="Killed\n",
                combined="Killed\n",
                exit_code=-15,
                signal="15",
            ),
        )
        tools = ProcessManagementTools(env=env)

        # Start a process first
        start_result = await tools.start_process(agent_ctx, command="killed_proc")
        process_id = extract_process_id(start_result)

        result = await tools.wait_for_process(agent_ctx, process_id)
        # Tools now return formatted strings
        assert isinstance(result, str)
        assert "Killed" in result


if __name__ == "__main__":
    pytest.main([__file__, "-vv"])
