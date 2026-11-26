"""Test integration of ProcessManagementTools with MockExecutionEnvironment."""

from typing import TYPE_CHECKING, Any

from anyenv.process_manager.models import ProcessOutput
from exxec import MockExecutionEnvironment
from exxec.models import ExecutionResult
import pytest

from agentpool import Agent, AgentContext
from agentpool.agents.events import ToolCallProgressEvent
from agentpool_toolsets.builtin.execution_environment import ProcessManagementTools


if TYPE_CHECKING:
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


@pytest.fixture
def mock_env() -> MockExecutionEnvironment:
    """Create mock execution environment with predefined responses."""
    return MockExecutionEnvironment(
        command_results={
            "echo hello world": ExecutionResult(
                result=None,
                duration=0.01,
                success=True,
                stdout="hello world\n",
                exit_code=0,
            ),
        },
        process_outputs={
            "echo": ProcessOutput(
                stdout="hello world\n",
                stderr="",
                combined="hello world\n",
                exit_code=0,
            ),
            "sleep": ProcessOutput(
                stdout="",
                stderr="",
                combined="",
                exit_code=0,
            ),
        },
    )


@pytest.fixture
def execution_tools(mock_env: MockExecutionEnvironment) -> ProcessManagementTools:
    """Create execution environment tools with mock environment."""
    return ProcessManagementTools(env=mock_env, name="test_execution_tools")


async def test_start_process(
    execution_tools: ProcessManagementTools,
    agent_ctx: AgentContext,
    test_agent: Agent[None],
):
    """Test starting a process through execution tools."""
    tools = await execution_tools.get_tools()
    start_tool = next(tool for tool in tools if tool.name == "start_process")

    result = await start_tool.run(
        agent_ctx=agent_ctx,
        command="echo",
        args=["hello", "world"],
        cwd="/tmp",
    )

    # Tools now return formatted strings
    assert isinstance(result, str)
    assert "Started background process" in result
    assert "mock_" in result
    assert "echo" in result

    # Check event was emitted to the queue
    events = get_progress_events(test_agent)
    assert len(events) == 1
    assert events[0].title is not None
    assert "Running: echo" in events[0].title


async def test_get_process_output(
    execution_tools: ProcessManagementTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
    test_agent: Agent[None],
):
    """Test getting process output."""
    # Start a process first
    process_id = await mock_env.process_manager.start_process("echo", ["hello", "world"])

    tools = await execution_tools.get_tools()
    output_tool = next(tool for tool in tools if tool.name == "get_process_output")

    result = await output_tool.run(
        agent_ctx=agent_ctx,
        process_id=process_id,
    )

    # Tools now return formatted strings
    assert isinstance(result, str)
    assert "hello world" in result

    # Check event was emitted: all output so far, for the process's terminal
    events = get_progress_events(test_agent)
    assert len(events) == 1
    signal = events[0].process
    assert signal is not None
    assert signal.snapshot
    assert signal.output is not None
    assert "hello world" in signal.output


async def test_kill_process(
    execution_tools: ProcessManagementTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
    test_agent: Agent[None],
):
    """Test killing a process."""
    # Start a process first
    process_id = await mock_env.process_manager.start_process("sleep", ["10"])

    tools = await execution_tools.get_tools()
    kill_tool = next(tool for tool in tools if tool.name == "kill_process")

    result = await kill_tool.run(
        agent_ctx=agent_ctx,
        process_id=process_id,
    )

    # Tools now return formatted strings
    assert isinstance(result, str)
    assert process_id in result
    assert "terminated" in result.lower()

    # Check event was emitted
    events = get_progress_events(test_agent)
    assert len(events) == 1
    assert events[0].title is not None
    assert "Killed process" in events[0].title
    assert process_id in events[0].title


async def test_wait_for_process(
    execution_tools: ProcessManagementTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
    test_agent: Agent[None],
):
    """Test waiting for process completion."""
    # Start a process first
    process_id = await mock_env.process_manager.start_process("echo", ["test"])

    tools = await execution_tools.get_tools()
    wait_tool = next(tool for tool in tools if tool.name == "wait_for_process")

    result = await wait_tool.run(
        agent_ctx=agent_ctx,
        process_id=process_id,
    )

    # Tools now return formatted strings
    assert isinstance(result, str)
    assert "hello world" in result  # The mock returns "hello world\n"

    # Check event was emitted
    events = get_progress_events(test_agent)
    assert len(events) == 1
    assert events[0].title is None  # The title stays the command
    assert events[0].process is not None
    assert events[0].process.exit_code == 0


async def test_release_process(
    execution_tools: ProcessManagementTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
    test_agent: Agent[None],
):
    """Test releasing process resources."""
    # Start a process first
    process_id = await mock_env.process_manager.start_process("echo", ["test"])

    tools = await execution_tools.get_tools()
    release_tool = next(tool for tool in tools if tool.name == "release_process")

    result = await release_tool.run(
        agent_ctx=agent_ctx,
        process_id=process_id,
    )

    # Tools now return formatted strings
    assert isinstance(result, str)
    assert process_id in result
    assert "released" in result.lower()

    # Verify process is no longer tracked
    processes = await mock_env.process_manager.list_processes()
    assert process_id not in processes

    # Check event was emitted
    events = get_progress_events(test_agent)
    assert len(events) == 1
    assert events[0].title is not None
    assert "Released process" in events[0].title
    assert process_id in events[0].title


async def test_list_processes(
    execution_tools: ProcessManagementTools,
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
):
    """Test listing all processes."""
    # Start some processes
    pid1 = await mock_env.process_manager.start_process("echo", ["1"])
    pid2 = await mock_env.process_manager.start_process("echo", ["2"])

    tools = await execution_tools.get_tools()
    list_tool = next(tool for tool in tools if tool.name == "list_processes")

    result = await list_tool.run(agent_ctx=agent_ctx)

    # Tools now return formatted strings
    assert isinstance(result, str)
    assert pid1 in result
    assert pid2 in result
    assert "Active processes" in result


async def test_execute_command(
    mock_env: MockExecutionEnvironment,
    agent_ctx: AgentContext,
    test_agent: Agent[None],
):
    """Test executing a command directly."""
    from agentpool.tool_impls.bash import create_bash_tool

    bash_tool = create_bash_tool(env=mock_env)

    result = await bash_tool.execute_and_unwrap(
        ctx=agent_ctx,
        command="echo hello world",
    )

    # Tools now return formatted strings
    assert isinstance(result, str)
    assert "hello world" in result

    # Check events were emitted (start + output + exit)
    events = get_progress_events(test_agent)
    assert len(events) >= 1  # At least process start event


async def test_process_not_found(
    execution_tools: ProcessManagementTools,
    agent_ctx: AgentContext,
):
    """Test error handling for non-existent process."""
    tools = await execution_tools.get_tools()
    output_tool = next(tool for tool in tools if tool.name == "get_process_output")

    result = await output_tool.run(
        agent_ctx=agent_ctx,
        process_id="nonexistent_process",
    )

    # Tools now return formatted strings
    assert isinstance(result, str)
    assert "Error" in result or "not found" in result.lower()


if __name__ == "__main__":
    pytest.main(["-v", __file__])
