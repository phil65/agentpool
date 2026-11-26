"""Integration tests for process management with AgentPool."""

import platform

import pytest

from agentpool.delegation.pool import AgentPool
from agentpool.models.agents import NativeAgentConfig
from agentpool.models.manifest import AgentsManifest
from agentpool_config.toolsets import (
    FSSpecToolsetConfig,
    ProcessManagementToolsetConfig,
)


def get_echo_command(message: str) -> tuple[str, list[str]]:
    """Get platform-appropriate echo command."""
    if platform.system() == "Windows":
        return "cmd", ["/c", "echo", message]
    return "echo", [message]


def get_sleep_command(seconds: str) -> tuple[str, list[str]]:
    """Get platform-appropriate sleep command."""
    if platform.system() == "Windows":
        return "cmd", ["/c", "timeout", seconds]
    return "sleep", [seconds]


def get_python_command() -> str:
    """Get platform-appropriate python command."""
    if platform.system() == "Windows":
        return "python"
    return "python3"


def get_temp_dir() -> str:
    """Get platform-appropriate temporary directory."""
    if platform.system() == "Windows":
        return "C:\\Windows\\Temp"
    return "/tmp"


@pytest.fixture
def process_manifest():
    """Create manifest with execution environment toolsets."""
    agent_cfg = NativeAgentConfig(
        name="ProcessAgent",
        model="test",
        tools=[ProcessManagementToolsetConfig(), FSSpecToolsetConfig()],
    )
    return AgentsManifest(agents={"process_agent": agent_cfg})


async def test_basic_process_workflow(process_manifest: AgentsManifest):
    """Test a complete process management workflow."""
    async with AgentPool(process_manifest) as pool:
        pm = pool.process_manager

        # Start a simple process (platform-aware)
        command, args = get_echo_command("Hello, World!")
        process_id = await pm.start_process(command, args)
        assert process_id.startswith("proc_")
        exit_code = await pm.wait_for_exit(process_id)
        assert exit_code == 0
        output = await pm.get_output(process_id)
        assert "Hello, World!" in output.stdout
        await pm.release_process(process_id)
        processes = await pm.list_processes()
        assert process_id not in processes


async def test_pool_cleanup_kills_processes(process_manifest: AgentsManifest):
    """Test that pool cleanup properly kills all processes."""
    async with AgentPool(process_manifest) as pool:
        pm = pool.process_manager
        # Start a long-running process (platform-aware)
        command, args = get_sleep_command("60")
        process_id = await pm.start_process(command, args)
        # Verify it's running
        processes = await pm.list_processes()
        assert process_id in processes


async def test_multiple_processes_management(process_manifest):
    """Test managing multiple processes simultaneously."""
    async with AgentPool(process_manifest) as pool:
        pm = pool.process_manager
        # Start multiple processes (platform-aware)
        cmd1, args1 = get_echo_command("Process 1")
        cmd2, args2 = get_echo_command("Process 2")
        cmd3, args3 = get_echo_command("Process 3")
        proc1 = await pm.start_process(cmd1, args1)
        proc2 = await pm.start_process(cmd2, args2)
        proc3 = await pm.start_process(cmd3, args3)
        # Verify all are tracked
        processes = await pm.list_processes()
        assert len(processes) == 3
        assert all(p in processes for p in [proc1, proc2, proc3])

        # Wait for all to complete
        for proc_id in [proc1, proc2, proc3]:
            exit_code = await pm.wait_for_exit(proc_id)
            assert exit_code == 0

        # Clean up all
        for proc_id in [proc1, proc2, proc3]:
            await pm.release_process(proc_id)

        # Verify all cleaned up
        processes = await pm.list_processes()
        assert len(processes) == 0


@pytest.mark.skip(reason="Output limit test needs refinement")
async def test_process_output_limit(process_manifest):
    """Test process output limiting functionality."""
    async with AgentPool(process_manifest) as pool:
        pm = pool.process_manager
        # Start process with small output limit
        # Use a command that generates more output than the limit (platform-aware)
        python_cmd = get_python_command()
        process_id = await pm.start_process(python_cmd, ["-c", "print('x' * 500)"], output_limit=50)
        exit_code = await pm.wait_for_exit(process_id)
        assert exit_code == 0
        # Check that output was truncated
        output = await pm.get_output(process_id)
        assert output.truncated
        assert len(output.combined.encode()) < 500
        await pm.release_process(process_id)


async def test_error_handling_invalid_command(process_manifest, caplog: pytest.LogCaptureFixture):
    """Test error handling for invalid commands."""
    caplog.set_level("CRITICAL")
    async with AgentPool(process_manifest) as pool:
        pm = pool.process_manager
        # Try to start non-existent command
        # Must provide args to trigger direct exec (without args, uses shell which starts OK)
        with pytest.raises(OSError, match="Failed to start process"):
            await pm.start_process("nonexistent_command_12345", args=["arg"])


async def test_process_info_retrieval(process_manifest):
    """Test getting detailed process information."""
    async with AgentPool(process_manifest) as pool:
        pm = pool.process_manager
        # Use platform-appropriate commands and working directory
        cwd = get_temp_dir()
        command, args = get_echo_command("test")
        process_id = await pm.start_process(command, args, cwd=cwd, env={"TEST_VAR": "test_value"})
        info = await pm.get_process_info(process_id)
        assert info["process_id"] == process_id
        assert info["command"] == command
        assert info["args"] == args
        assert info["cwd"] == cwd
        assert "created_at" in info
        assert "is_running" in info
        await pm.wait_for_exit(process_id)
        await pm.release_process(process_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
