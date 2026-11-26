"""Snapshot tests for agentpool native Agent through ACP protocol.

This tests the full wire format by spawning agentpool serve-acp as a subprocess
and capturing the session updates received by the ACP client.

This complements the direct harness tests by verifying the protocol layer:
- Direct harness: Tests ACPSession internals → notifications
- This test: Tests full subprocess → JSON-RPC → client event flow
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
import sys
import tempfile
from typing import TYPE_CHECKING, Any

from exxec.models import ExecutionResult
from exxec_config import MockExecutionEnvironmentConfig
import pytest
from syrupy.extensions.json import JSONSnapshotExtension
import yaml

from agentpool.delegation import AgentPool
from agentpool.models import ACPAgentConfig
from agentpool_config.agentpool_tools import BashToolConfig


if TYPE_CHECKING:
    from collections.abc import Sequence

    from syrupy.assertion import SnapshotAssertion

    from agentpool.models.agents import AnyToolConfig


# Skip on Windows due to temp file locking issues with subprocesses
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Windows temp file locking")


@pytest.fixture
def json_snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Configure snapshot to use JSON format."""
    return snapshot.use_extension(JSONSnapshotExtension)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def create_server_config_file(temp_dir: Path, tool_name: str, tool_args: dict[str, Any]) -> Path:
    """Create a simple server config with Native Agent (no tools).

    The server agent is "dumb" - it has no tools. Tools are provided via MCP bridge.

    Args:
        temp_dir: Directory to write config file to
        tool_name: Name of the tool to call (for test model)
        tool_args: Arguments for the tool (for test model)
    """
    agent_config: dict[str, Any] = {
        "type": "native",
        "model": {
            "type": "test",
            "call_tools": [tool_name],
            "tool_args": {tool_name: tool_args},
        },
        # NO tools defined - they come from MCP bridge
    }

    config = {"agents": {"test_agent": agent_config}}
    config_path = temp_dir / "server_config.yml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


def create_client_config_file(
    temp_dir: Path,
    server_config_path: Path,
    tools: Sequence[AnyToolConfig | str],
    mock_env: MockExecutionEnvironmentConfig,
) -> Path:
    """Create client config that spawns ACP server with MCP bridge.

    Args:
        temp_dir: Directory to write config file to
        server_config_path: Path to the server config file
        tools: Tool configs with environment (will be provided via MCP bridge)
        mock_env: Mock execution environment for deterministic IDs
    """
    agent_config = ACPAgentConfig(
        command="uv",
        args=[
            "run",
            "agentpool",
            "serve-acp",
            str(server_config_path),
            "--agent",
            "test_agent",
        ],
        tools=tools,
    )
    config = {"agents": {"test_client": agent_config.model_dump()}}
    config_path = temp_dir / "client_config.yml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


@dataclass
class ACPViaACPHarness:
    """Test harness for capturing events from agentpool-via-ACP.

    Uses MockExecutionEnvironment with deterministic_ids for stable snapshots,
    matching the approach used by the native agent test harness.
    """

    temp_dir: Path
    recorded_events: list[dict[str, Any]] = field(default_factory=list)

    async def execute_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        tools: Sequence[AnyToolConfig | str],
    ) -> list[dict[str, Any]]:
        """Execute a tool via ACP subprocess with MCP bridge and capture events.

        This creates:
        1. Server config: "dumb" Native Agent with no tools
        2. Client config: ACP agent that spawns the server and provides tools via MCP bridge

        Args:
            tool_name: Name of tool to execute
            tool_args: Arguments to pass to tool
            tools: Tool configurations (provided to client, bridged to server via MCP)
        """
        # Create server config (dumb agent with no tools)
        server_config_path = create_server_config_file(self.temp_dir, tool_name, tool_args)
        # Extract mock environment from first tool (all should have same env)
        mock_env = None
        for tool in tools:
            if hasattr(tool, "environment") and (env := tool.environment):
                mock_env = env
                break
        if not mock_env:
            # Default mock env with deterministic IDs
            mock_env = MockExecutionEnvironmentConfig(deterministic_ids=True)
        assert isinstance(mock_env, MockExecutionEnvironmentConfig)

        # Create client config (ACP agent with tools that will be bridged)
        client_config_path = create_client_config_file(
            self.temp_dir,
            server_config_path,
            tools=tools,
            mock_env=mock_env,
        )

        self.recorded_events.clear()
        # Use AgentPool to instantiate the client agent from config
        async with AgentPool(manifest=client_config_path) as pool:
            # ACP agents are in pool.all_agents dict
            agent = pool.all_agents["test_client"]
            async for event in agent.run_stream("Execute the tool"):
                event_dict = asdict(event)
                event_dict["type"] = type(event).__name__
                self.recorded_events.append(event_dict)

        return self.recorded_events


@pytest.fixture
def harness(temp_dir: Path) -> ACPViaACPHarness:
    """Create test harness."""
    return ACPViaACPHarness(temp_dir=temp_dir)


class TestExecuteCommandViaACP:
    """Test execute_command tool through ACP subprocess."""

    async def test_execute_command_simple(
        self,
        harness: ACPViaACPHarness,
        json_snapshot: SnapshotAssertion,
    ):
        """Test simple command execution via ACP with mock environment."""
        exec_result = ExecutionResult(
            result=None,
            stdout="hello\n",
            stderr="",
            success=True,
            exit_code=0,
            duration=0.01,
        )
        results = {"echo hello": asdict(exec_result)}
        mock_env = MockExecutionEnvironmentConfig(deterministic_ids=True, command_results=results)
        events = await harness.execute_tool(
            tool_name="bash",
            tool_args={"command": "echo hello"},
            tools=[BashToolConfig(environment=mock_env)],
        )
        # Filter to tool call messages for stable comparison
        tool_events = [
            e
            for e in events
            if e["type"] in ("ToolCallStartEvent", "ToolCallProgressEvent", "ToolCallCompleteEvent")
        ]

        assert tool_events == json_snapshot


# class TestExecuteCodeViaACP:
#     """Test execute_code tool through ACP subprocess."""

#     async def test_execute_code_simple(
#         self,
#         harness: ACPViaACPHarness,
#         json_snapshot: SnapshotAssertion,
#     ):
#         """Test simple code execution via ACP with mock environment."""
#         mock_env = MockExecutionEnvironmentConfig(
#             deterministic_ids=True,
#             code_results={
#                 "print('hello')": asdict(
#                     ExecutionResult(
#                         result=None,
#                         stdout="hello\n",
#                         stderr="",
#                         success=True,
#                         exit_code=0,
#                         duration=0.01,
#                     )
#                 )
#             },
#         )

#         events = await harness.execute_tool(
#             tool_name="execute_code",
#             tool_args={"code": "print('hello')", "title": "test hello"},
#             tools=[ExecuteCodeToolConfig(environment=mock_env)],
#         )

#         # Filter to tool call messages for stable comparison
#         tool_events = [
#             e
#             for e in events
#             if e["type"]
#             in (
#                 "ToolCallStartEvent",
#                 "ToolCallProgressEvent",
#                 "ToolCallCompleteEvent",
#             )
#         ]

#         assert tool_events == json_snapshot


if __name__ == "__main__":
    pytest.main([__file__, "-vv"])
