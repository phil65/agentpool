"""Snapshot tests for tool call JSON-RPC messages using full ACPSession flow.

These tests use the ToolCallTestHarness to capture the exact wire format of
JSON-RPC notifications for regression testing.
"""

from typing import TYPE_CHECKING

from exxec.models import ExecutionResult
import pytest
from syrupy.extensions.json import JSONSnapshotExtension

from agentpool_config.agentpool_tools import BashToolConfig, ExecuteCodeToolConfig
from agentpool_config.toolsets import FSSpecToolsetConfig

from .tool_call_harness import ToolCallTestHarness


if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


@pytest.fixture
def json_snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Use JSON serialization for cleaner snapshots."""
    return snapshot.use_extension(JSONSnapshotExtension)


@pytest.fixture
def harness() -> ToolCallTestHarness:
    """Create a fresh test harness for each test."""
    return ToolCallTestHarness()


class TestReadFileSnapshots:
    """Snapshot tests for read_file tool."""

    async def test_read_file_basic(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test basic file read produces expected notifications."""
        await harness.mock_env.set_file_content("/test/hello.txt", "Hello, World!")

        messages = await harness.execute_tool(
            tool_name="read",
            tool_args={"path": "/test/hello.txt"},
            tools=[FSSpecToolsetConfig()],
        )

        assert messages == json_snapshot

    async def test_read_file_with_line_range(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test file read with line/limit produces expected notifications."""
        content = "\n".join(f"Line {i}" for i in range(1, 11))
        await harness.mock_env.set_file_content("/test/lines.txt", content)

        messages = await harness.execute_tool(
            tool_name="read",
            tool_args={"path": "/test/lines.txt", "line": 3, "limit": 2},
            tools=[FSSpecToolsetConfig()],
        )

        assert messages == json_snapshot


class TestWriteFileSnapshots:
    """Snapshot tests for write_file tool."""

    async def test_write_file_new(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test writing a new file produces expected notifications."""
        messages = await harness.execute_tool(
            tool_name="write",
            tool_args={"path": "/test/new_file.txt", "content": "New content here"},
            tools=[FSSpecToolsetConfig()],
        )

        assert messages == json_snapshot

    async def test_write_file_overwrite(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test overwriting existing file produces expected notifications."""
        await harness.mock_env.set_file_content("/test/existing.txt", "Old content")

        messages = await harness.execute_tool(
            tool_name="write",
            tool_args={
                "path": "/test/existing.txt",
                "content": "Updated content",
                "overwrite": True,
            },
            tools=[FSSpecToolsetConfig()],
        )

        assert messages == json_snapshot


class TestExecuteCodeSnapshots:
    """Snapshot tests for execute_code tool."""

    async def test_execute_code_simple(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test simple code execution produces expected notifications."""
        harness.mock_env._code_results["print('hello')"] = ExecutionResult(
            result=None, duration=0.01, success=True, stdout="hello\n", exit_code=0
        )

        messages = await harness.execute_tool(
            tool_name="execute_code",
            tool_args={"code": "print('hello')", "title": "test hello"},
            tools=[ExecuteCodeToolConfig()],
        )

        assert messages == json_snapshot

    async def test_execute_code_with_error(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test code execution with error produces expected notifications."""
        harness.mock_env._code_results["raise ValueError('test error')"] = ExecutionResult(
            result=None,
            duration=0.01,
            success=False,
            stderr="ValueError: test error\n",
            exit_code=1,
            error="ValueError: test error",
            error_type="ValueError",
        )

        messages = await harness.execute_tool(
            tool_name="execute_code",
            tool_args={"code": "raise ValueError('test error')", "title": "test error"},
            tools=[ExecuteCodeToolConfig()],
        )

        assert messages == json_snapshot

    async def test_execute_code_multiline(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test multiline code execution produces expected notifications."""
        code = "x = 1\ny = 2\nprint(x + y)"
        harness.mock_env._code_results[code] = ExecutionResult(
            result=None, duration=0.01, success=True, stdout="3\n", exit_code=0
        )

        messages = await harness.execute_tool(
            tool_name="execute_code",
            tool_args={"code": code, "title": "test multiline"},
            tools=[ExecuteCodeToolConfig()],
        )

        assert messages == json_snapshot


class TestExecuteCommandSnapshots:
    """Snapshot tests for execute_command tool."""

    async def test_execute_command_simple(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test simple command execution produces expected notifications."""
        harness.mock_env._command_results["echo hello"] = ExecutionResult(
            result=None, duration=0.01, success=True, stdout="hello\n", exit_code=0
        )

        messages = await harness.execute_tool(
            tool_name="bash",
            tool_args={"command": "echo hello"},
            tools=[BashToolConfig()],
        )

        assert messages == json_snapshot

    async def test_execute_command_with_stderr(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test command with stderr produces expected notifications."""
        harness.mock_env._command_results["ls /nonexistent"] = ExecutionResult(
            result=None,
            duration=0.01,
            success=False,
            stderr="ls: cannot access '/nonexistent': No such file or directory\n",
            exit_code=2,
            error="Command failed",
            error_type="CommandError",
        )

        messages = await harness.execute_tool(
            tool_name="bash",
            tool_args={"command": "ls /nonexistent"},
            tools=[BashToolConfig()],
        )

        assert messages == json_snapshot

    async def test_execute_command_with_output_limit(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test command with output limit produces expected notifications."""
        long_output = "line\n" * 100
        harness.mock_env._command_results["cat bigfile"] = ExecutionResult(
            result=None, duration=0.01, success=True, stdout=long_output, exit_code=0
        )

        messages = await harness.execute_tool(
            tool_name="bash",
            tool_args={"command": "cat bigfile", "output_limit": 50},
            tools=[BashToolConfig()],
        )

        assert messages == json_snapshot


class TestEditFileSnapshots:
    """Snapshot tests for edit tool (file editing with diff)."""

    async def test_edit_file_simple(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test simple file edit produces expected notifications with diff content."""
        # Set up initial file content
        await harness.mock_env.set_file_content(
            "/test/example.py", "def old_function():\n    pass\n"
        )

        messages = await harness.execute_tool(
            tool_name="edit",
            tool_args={
                "path": "/test/example.py",
                "old_string": "def old_function():",
                "new_string": "def new_function():",
                "description": "Rename function from old to new",
            },
            tools=[FSSpecToolsetConfig()],
        )

        assert messages == json_snapshot

    async def test_edit_file_with_replace_all(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test edit with replace_all produces expected notifications."""
        content = "def func1():\n    func1()\n\ndef func2():\n    func1()\n"
        await harness.mock_env.set_file_content("/test/multi.py", content)

        messages = await harness.execute_tool(
            tool_name="edit",
            tool_args={
                "path": "/test/multi.py",
                "old_string": "func1",
                "new_string": "renamed",
                "description": "Replace all func1 occurrences",
                "replace_all": True,
            },
            tools=[FSSpecToolsetConfig()],
        )

        assert messages == json_snapshot


class TestMCPToolSnapshots:
    """Snapshot tests for MCP tool calls."""

    async def test_mcp_tool_with_progress(
        self,
        harness: ToolCallTestHarness,
        json_snapshot: SnapshotAssertion,
    ) -> None:
        """Test MCP tool with progress notifications produces expected messages."""
        from pathlib import Path

        from agentpool_config.mcp_server import StdioMCPServerConfig

        server_path = Path(__file__).parent.parent.parent / "mcp_server" / "server.py"
        mcp_server = StdioMCPServerConfig(
            name="test_server",
            command="uv",
            args=["run", str(server_path)],
        )

        messages = await harness.execute_tool(
            tool_name="test_progress",
            tool_args={"message": "hello"},
            mcp_servers=[mcp_server],
        )

        assert messages == json_snapshot


if __name__ == "__main__":
    pytest.main(["-v", __file__])
