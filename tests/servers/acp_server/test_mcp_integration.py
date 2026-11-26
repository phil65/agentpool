"""Test MCP server integration with ACP sessions."""

import sys
import tempfile
from typing import TYPE_CHECKING

from aclientp import EnvVariable, StdioMcpServer
import pytest

from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool.log import get_logger
from agentpool.tools.base import FunctionTool
from agentpool_server.acp_server.converters import convert_acp_mcp_server_to_config
from agentpool_server.acp_server.session import ACPSession
from agentpool_server.acp_server.session_manager import ACPSessionManager


if TYPE_CHECKING:
    from aclientp import ClientCapabilities

    from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


logger = get_logger(__name__)


async def test_mcp_server_conversion():
    """Test conversion from ACP McpServer to our MCPServerConfig."""
    acp_server = StdioMcpServer(
        name="test_server",
        command="uv",
        args=["run", "test-mcp-server"],
        env=[
            EnvVariable(name="API_KEY", value="test123"),
            EnvVariable(name="DEBUG", value="true"),
        ],
    )
    config = convert_acp_mcp_server_to_config(acp_server)
    assert config.name == "test_server"
    assert config.command == "uv"
    assert config.args == ["run", "test-mcp-server"]
    assert config.env == {"API_KEY": "test123", "DEBUG": "true"}


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "darwin", reason="macOS subprocess handling differs")
async def test_session_with_mcp_servers(
    test_client,
    acp_agent: AgentPoolACPAgent,
    client_capabilities: ClientCapabilities,
):
    """Test creating an ACP session with MCP servers."""
    agent_pool = AgentPool()

    def simple_callback(message: str) -> str:
        return f"Test response for: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=agent_pool)
    agent_pool.register("test_agent", agent)

    # Sample MCP servers (these won't actually connect in the test)
    mcp_servers = [
        StdioMcpServer(
            name="filesystem",
            command="npx",  # Use echo as a dummy command
            args=["-y", "@upstash/context7-mcp"],
            env=[],
        ),
        StdioMcpServer(
            name="web_search",
            command="uvx",  # Use echo as a dummy command
            args=["mcp-server-git"],
            env=[EnvVariable(name="API_KEY", value="dummy")],
        ),
    ]

    session = ACPSession(  # Create session with MCP servers
        session_id="test_session",
        agent=agent,
        cwd=tempfile.gettempdir(),
        client=test_client,
        mcp_servers=mcp_servers,
        acp_agent=acp_agent,
        client_capabilities=client_capabilities,
    )

    assert session.session_id == "test_session"
    assert session.mcp_servers == mcp_servers

    # Test initialization (this will fail without real MCP servers, which is expected)
    try:
        await session.initialize_mcp_servers()
        print("✓ MCP servers initialized (unexpectedly succeeded)")
    except Exception as e:  # noqa: BLE001
        print(f"✓ MCP server initialization failed as expected: {type(e).__name__}")

    await session.close()


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS subprocess handling differs")
async def test_session_manager_with_mcp(
    test_client,
    acp_agent: AgentPoolACPAgent,
    client_capabilities: ClientCapabilities,
):
    """Test session manager creating sessions with MCP servers."""
    agent_pool = AgentPool()  # Create empty pool and register the agent
    session_manager = ACPSessionManager(agent_pool)

    def simple_callback(message: str) -> str:
        return f"Test response for: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=agent_pool)
    agent_pool.register("test_agent", agent)
    mcp_servers = [StdioMcpServer(name="tools", command="echo", args=["tools"], env=[])]
    async with agent_pool:
        try:
            created = await session_manager.create_session(
                agent=agent,
                cwd=tempfile.gettempdir(),
                client=test_client,
                mcp_servers=mcp_servers,
                acp_agent=acp_agent,
                client_capabilities=client_capabilities,
            )

            session = session_manager.get_session(created.session_id)
            assert session is created
            assert session.mcp_servers == mcp_servers
            await session_manager.close_session(created.session_id)

        except Exception:
            logger.exception("Session manager test failed")
            raise


async def test_tool_integration():
    """Test that MCP tools would be properly integrated."""

    def simple_callback(message: str) -> str:
        return f"Test response for: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback)

    async with agent:
        initial_tools = len(await agent.tools.get_tools())
        # In real scenario, MCP tools would be added here
        # For test, we'll simulate by adding a dummy tool

        def dummy_mcp_tool(query: str) -> str:
            """Dummy MCP tool for testing."""
            return f"MCP result for: {query}"

        meta = {"mcp_tool": "dummy_search"}
        tool = FunctionTool.from_callable(dummy_mcp_tool, source="mcp", meta=meta)

        agent.tools.register_tool(tool)

        final_tools = len(await agent.tools.get_tools())
        assert final_tools == initial_tools + 1


if __name__ == "__main__":
    pytest.main(["-v", __file__])
