"""Integration tests for ACPAgent connecting to real agentpool ACP server.

These tests spawn an actual agentpool serve-acp process with a TestModel
and connect to it using ACPAgent, testing the full roundtrip.

Note: These are slow integration tests that spawn real subprocesses.
Run with: pytest tests/servers/acp_server/test_acp_agent_integration.py -v
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

import anyio
import pytest

from agentpool.agents.acp_agent import ACPAgent
from agentpool.models.acp_agents import ACPAgentConfig


if TYPE_CHECKING:
    from agentpool.agents.events import RichAgentStreamEvent


# Skip on Windows due to subprocess/asyncio compatibility issues
pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="ACP subprocess tests hang on Windows"),
]


@pytest.fixture
def test_agent_config_yaml() -> str:
    """YAML config for a minimal test agent using TestModel."""
    return """
agents:
  test_agent:
    type: native
    model: test
"""


@pytest.fixture
def test_config_file(test_agent_config_yaml: str, tmp_path: Path) -> Path:
    """Create a temporary config file for testing."""
    config_file = tmp_path / "test_config.yml"
    config_file.write_text(test_agent_config_yaml)
    return config_file


@pytest.fixture
def acp_agent_config(test_config_file: Path) -> ACPAgentConfig:
    """Create ACPAgentConfig that spawns agentpool serve-acp."""
    return ACPAgentConfig(
        command="uv",
        args=[
            "run",
            "agentpool",
            "serve-acp",
            str(test_config_file),
            "--agent",
            "test_agent",
        ],
        name="test_acp_agent",
        description="Test ACP agent with TestModel",
        cwd=str(Path.cwd()),
    )


async def test_acp_agent_basic_prompt(acp_agent_config: ACPAgentConfig, test_config_file: Path):
    """Test basic prompt execution through ACP."""
    try:
        print(f"\nConfig file: {test_config_file}")
        print(f"Config file exists: {test_config_file.exists()}")
        async with ACPAgent.from_config(acp_agent_config) as agent:
            with anyio.fail_after(15.0):
                result = await agent.run("Hi")

            assert result is not None
            assert result.content is not None
            assert len(result.content) > 0
    except TimeoutError:
        pytest.skip("ACP server took too long to respond")


async def test_acp_agent_streaming(acp_agent_config: ACPAgentConfig):
    """Test streaming response through ACP."""
    try:
        async with ACPAgent.from_config(acp_agent_config) as agent:
            chunks: list[RichAgentStreamEvent[Any]] = []

            with anyio.fail_after(15.0):
                async for chunk in agent.run_stream("Hi"):
                    chunks.append(chunk)  # noqa: PERF401

            assert len(chunks) > 2
    except TimeoutError:
        pytest.skip("ACP server took too long to respond")


async def test_acp_agent_multiple_prompts(acp_agent_config: ACPAgentConfig):
    """Test multiple sequential prompts in same session."""
    try:
        async with ACPAgent.from_config(acp_agent_config) as agent:
            with anyio.fail_after(15.0):
                result1 = await agent.run("One")
            assert result1.content is not None

            with anyio.fail_after(15.0):
                result2 = await agent.run("Two")
            assert result2.content is not None
    except TimeoutError:
        pytest.skip("ACP server took too long to respond")


async def test_acp_agent_session_info(acp_agent_config: ACPAgentConfig):
    """Test that session info is properly initialized."""
    try:
        async with ACPAgent.from_config(acp_agent_config) as agent:
            assert agent._sdk_session_id is not None
            assert agent._agent_info is not None
            assert agent._state is not None
    except TimeoutError:
        pytest.skip("ACP server took too long to start")


async def test_acp_agent_file_operations(tmp_path: Path, test_config_file: Path):
    """Test file operations through ACP when enabled."""
    # Create a test file
    test_file = tmp_path / "test_input.txt"
    test_file.write_text("Hello from test file")

    config = ACPAgentConfig(
        command="uv",
        args=[
            "run",
            "agentpool",
            "serve-acp",
            str(test_config_file),
            "--agent",
            "test_agent",
        ],
        name="test_acp_file_agent",
        cwd=str(tmp_path),
        allow_file_operations=True,
    )

    async with ACPAgent.from_config(config) as agent:
        # The TestModel will just respond, but the file access capability should be enabled
        assert agent._client_handler is not None
        assert agent._client_handler.allow_file_operations is True


async def test_acp_agent_terminal_operations(tmp_path: Path, test_config_file: Path):
    """Test terminal operations through ACP when enabled."""
    config = ACPAgentConfig(
        command="uv",
        args=[
            "run",
            "agentpool",
            "serve-acp",
            str(test_config_file),
            "--agent",
            "test_agent",
        ],
        name="test_acp_terminal_agent",
        cwd=str(tmp_path),
        allow_terminal=True,
    )

    async with ACPAgent.from_config(config) as agent:
        assert agent._client_handler is not None
        assert agent._client_handler.allow_terminal is True


async def test_acp_agent_cleanup_on_error(acp_agent_config: ACPAgentConfig):
    """Test that resources are cleaned up even when errors occur."""
    agent = ACPAgent.from_config(acp_agent_config)
    # Enter context
    await agent.__aenter__()
    assert agent._process is not None
    process = agent._process
    # Exit context (simulating cleanup)
    await agent.__aexit__(None, None, None)
    # Process should be terminated
    assert agent._process is None
    # Give it a moment to fully terminate
    await anyio.sleep(0.1)
    assert process.returncode is not None


async def test_acp_agent_stats(acp_agent_config: ACPAgentConfig):
    """Test that agent stats are collected."""
    try:
        async with ACPAgent.from_config(acp_agent_config) as agent:
            with anyio.fail_after(15.0):
                await agent.run("Test")
            stats = await agent.get_stats()

            assert stats is not None
    except TimeoutError:
        pytest.skip("ACP server took too long to respond")


async def test_acp_agent_with_input_provider(acp_agent_config: ACPAgentConfig):
    """Test ACPAgent with custom input provider."""
    from agentpool.ui.stdlib_provider import StdlibInputProvider

    input_provider = StdlibInputProvider()

    try:
        # Test with input_provider in constructor
        async with ACPAgent.from_config(acp_agent_config, input_provider=input_provider) as agent:
            assert agent._input_provider is input_provider

            # Test that input_provider can be overridden per-run
            # Note: This is scoped to the run, doesn't permanently mutate agent
            new_provider = StdlibInputProvider()
            with anyio.fail_after(15.0):
                await agent.run("Test", input_provider=new_provider)
            # Agent keeps its original input_provider
            assert agent._input_provider is input_provider
            # But the session's ACP session uses it for the run's permission requests
            assert agent._state is not None
            assert agent._state.input_provider is new_provider

    except TimeoutError:
        pytest.skip("ACP server took too long to respond")


async def test_acp_agent_input_provider_in_run_stream(acp_agent_config: ACPAgentConfig):
    """Test input_provider parameter in run_stream method."""
    from agentpool.ui.stdlib_provider import StdlibInputProvider

    input_provider = StdlibInputProvider()

    try:
        async with ACPAgent.from_config(acp_agent_config) as agent:
            # Store original provider (might be None or default)
            original_provider = agent._input_provider
            chunks: list[RichAgentStreamEvent[Any]] = []

            with anyio.fail_after(15.0):
                async for chunk in agent.run_stream("Hi", input_provider=input_provider):
                    chunks.append(chunk)  # noqa: PERF401

            # Verify agent keeps original provider (input_provider is scoped to the run)
            assert agent._input_provider is original_provider
            # The session's ACP session uses it for the run's permission requests
            assert agent._state is not None
            assert agent._state.input_provider is input_provider

    except TimeoutError:
        pytest.skip("ACP server took too long to respond")


if __name__ == "__main__":
    pytest.main(["-v", __file__])
