"""Generic tool call test harness for snapshot testing ACP notifications.

This module provides a reusable test harness for capturing and snapshot testing
the JSON-RPC notifications produced by tool calls through the full ACPSession flow.

Example usage:
    ```python
    async def test_my_tool(harness: ToolCallTestHarness, snapshot):
        # Setup any required state
        await harness.mock_env.set_file_content("/test/file.txt", "content")

        # Execute tool and capture notifications
        messages = await harness.execute_tool(
            tool_name="my_tool",
            tool_args={"arg1": "value1"},
            tools=[MyToolsetConfig()],
        )

        assert messages == snapshot
    ```
"""

from dataclasses import dataclass, field
import tempfile
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

from aclientp import ClientCapabilities
from aclientp.client.implementations import HeadlessACPClient
from aclientp.schema import TextContentBlock
from exxec import MockExecutionEnvironment
from llmling_models_config import TestModelConfig

from agentpool import AgentsManifest
from agentpool.delegation import AgentPool
from agentpool.models.agents import NativeAgentConfig
from agentpool.utils.tasks import TaskManager
from agentpool_server.acp_server.session import ACPSession


if TYPE_CHECKING:
    from collections.abc import Sequence

    from aclientp.schema import SessionNotification

    from agentpool_config import AnyToolConfig
    from agentpool_config.mcp_server import MCPServerConfig


class RecordingACPClient(HeadlessACPClient):
    """HeadlessACPClient that records wire-format messages."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.wire_messages: list[dict[str, Any]] = []

    async def session_update(self, params: SessionNotification) -> None:
        """Capture wire format before delegating to parent."""
        dumped = params.model_dump(by_alias=True, exclude_none=True)
        update = dumped.get("update", {})
        update_type = update.get("sessionUpdate", "unknown")
        self.wire_messages.append({"type": update_type, "payload": update})
        await super().session_update(params)

    def clear(self) -> None:
        """Clear recorded wire messages."""
        self.wire_messages.clear()

    def get_tool_call_messages(self) -> list[dict[str, Any]]:
        """Get only tool call related messages."""
        return [
            msg for msg in self.wire_messages if msg["type"] in ("tool_call", "tool_call_update")
        ]

    def get_all_messages(self) -> list[dict[str, Any]]:
        """Get all recorded messages."""
        return self.wire_messages.copy()


@dataclass
class ToolCallTestHarness:
    """Generic test harness for tool call snapshot testing.

    Provides a reusable setup for testing any toolset's notifications through
    the full ACPSession flow.

    Attributes:
        mock_env: Mock execution environment with in-memory filesystem
        client: Recording client that captures notifications
        session_id: Identifier for the test session
    """

    mock_env: MockExecutionEnvironment = field(
        default_factory=lambda: MockExecutionEnvironment(deterministic_ids=True)
    )
    client: RecordingACPClient = field(
        default_factory=lambda: RecordingACPClient(
            allow_file_operations=True,
            auto_grant_permissions=True,
        )
    )
    session_id: str = "tool-call-test-session"
    _mock_acp_agent: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._mock_acp_agent = AsyncMock()
        self._mock_acp_agent.tasks = TaskManager()

    async def execute_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        tools: Sequence[AnyToolConfig | str] | None = None,
        mcp_servers: list[MCPServerConfig] | None = None,
        prompt: str = "Execute the tool",
    ) -> list[dict[str, Any]]:
        """Execute a tool and return the captured tool call messages.

        Args:
            tool_name: Name of the tool to call
            tool_args: Arguments to pass to the tool
            tools: List of tool/toolset configs that provide the tool
            mcp_servers: List of MCP server configs that provide the tool
            prompt: Text prompt to send (content doesn't matter, model is configured)

        Returns:
            List of tool call notification messages in wire format
        """
        # Create manifest with model configured to call the specific tool

        model_config = TestModelConfig(call_tools=[tool_name], tool_args={tool_name: tool_args})
        agent_config = NativeAgentConfig(
            name="harness_test_agent",
            model=model_config,
            tools=tools or [],
            mcp_servers=mcp_servers or [],
        )
        manifest = AgentsManifest(agents={"harness_test_agent": agent_config})
        # Create pool and session
        async with AgentPool(manifest) as pool:
            agent = pool.all_agents["harness_test_agent"]
            capabilities = ClientCapabilities(fs=None, terminal=False)
            session = ACPSession(
                session_id=self.session_id,
                agent=agent,
                cwd=tempfile.gettempdir(),
                client=self.client,
                acp_agent=self._mock_acp_agent,
                client_capabilities=capabilities,
            )
            # The session's environment: where its turns' tools run
            session.env = self.mock_env
            # Clear and execute
            self.client.clear()
            content_blocks = [TextContentBlock(text=prompt)]
            await session.process_prompt(content_blocks)
            return self.client.get_tool_call_messages()

    async def execute_tools(
        self,
        tool_calls: dict[str, dict[str, Any]],
        tools: Sequence[AnyToolConfig | str] | None = None,
        prompt: str = "Execute the tools",
    ) -> list[dict[str, Any]]:
        """Execute multiple tools and return the captured messages.

        Args:
            tool_calls: Dict mapping tool_name -> tool_args
            tools: List of tool/toolset configs that provide the tools
            prompt: Text prompt to send

        Returns:
            List of tool call notification messages in wire format
        """
        tool_names = list(tool_calls.keys())
        model_config = TestModelConfig(call_tools=tool_names, tool_args=tool_calls)
        agent_config = NativeAgentConfig(
            name="harness_test_agent",
            model=model_config,
            tools=tools or [],
        )
        manifest = AgentsManifest(agents={"harness_test_agent": agent_config})
        async with AgentPool(manifest) as pool:
            harness_agent = pool.all_agents["harness_test_agent"]
            capabilities = ClientCapabilities(fs=None, terminal=False)
            session = ACPSession(
                session_id=self.session_id,
                agent=harness_agent,
                cwd=tempfile.gettempdir(),
                client=self.client,
                acp_agent=self._mock_acp_agent,
                client_capabilities=capabilities,
            )

            session.env = self.mock_env  # Where its turns' tools run
            self.client.clear()
            content_blocks = [TextContentBlock(text=prompt)]
            await session.process_prompt(content_blocks)
            return self.client.get_tool_call_messages()
