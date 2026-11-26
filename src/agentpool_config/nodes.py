"""Team configuration models."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal, assert_never

from exxec_config import E2bExecutionEnvironmentConfig, ExecutionEnvironmentConfig
from pydantic import ConfigDict, Field, HttpUrl, ImportString
from schemez import Schema

from agentpool_config.event_handlers import EventHandlerConfig, StdoutEventHandlerConfig
from agentpool_config.events import EventConfig, FileWatchTriggerConfig, TimeTriggerConfig
from agentpool_config.forward_targets import (
    FileConnectionConfig,
    ForwardingTarget,
    NodeConnectionConfig,
)
from agentpool_config.hooks import HooksConfig
from agentpool_config.mcp_server import (
    BaseMCPServerConfig,
    MCPServerConfig,
    StreamableHTTPMCPServerConfig,
)


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

    from agentpool.common_types import IndividualEventHandler


ToolConfirmationMode = Literal["always", "never", "per_tool"]
"""Controls how permission requests are handled:

- "always": Always prompt user for confirmation
- "never": Auto-grant all permissions (no prompts)
- "per_tool": Use individual tool settings (treated as "always" for ACP)
"""


class NodeConfig(Schema):
    """Configuration for a Node of the messaging system."""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        json_schema_extra={
            "x-icon": "octicon:workflow-16",
            "x-doc-title": "Node Configuration",
        },
    )

    name: str | None = Field(default=None)
    """Identifier for the node. Set from dict key, not from YAML."""

    config_file_path: str | None = Field(
        default=None,
        exclude=True,
        examples=["/path/to/config.yml", "configs/agent.yaml"],
        title="Configuration file path",
    )
    """Config file path for resolving relative paths."""

    display_name: str | None = Field(
        default=None,
        examples=["Main Agent", "Web Searcher", "Code Assistant"],
        title="Display name",
    )
    """Human-readable display name for the node."""

    description: str | None = Field(
        default=None,
        examples=["Main conversation agent", "Handles web search requests"],
        title="Node description",
    )
    """Optional description of the agent / team."""

    triggers: list[EventConfig] = Field(
        default_factory=list,
        examples=[
            [
                TimeTriggerConfig(
                    name="daily_check",
                    schedule="0 9 * * *",
                    prompt="Daily status update",
                )
            ],
            [
                FileWatchTriggerConfig(
                    name="code_watcher",
                    paths=["./src"],
                    extensions=[".py"],
                )
            ],
        ],
        title="Event triggers",
    )
    """Event sources that activate this agent / team"""

    connections: list[ForwardingTarget] = Field(
        default_factory=list,
        examples=[
            [
                NodeConnectionConfig(name="output_agent", wait_for_completion=True),
            ],
            [
                FileConnectionConfig(path="logs/messages.txt", template="{{ message.content }}"),
            ],
        ],
        title="Message forwarding targets",
    )
    """Targets to forward results to."""

    mcp_servers: Sequence[str | MCPServerConfig] = Field(
        default_factory=list,
        title="MCP servers",
        examples=[
            ["uvx some-server"],
            [StreamableHTTPMCPServerConfig(url=HttpUrl("http://mcp.example.com"))],
        ],
    )
    """List of MCP server configurations:
    - str entries: URLs become HTTP / SSE servers, other strings stdio commands
    - MCPServerConfig for full server configuration
    """
    # Any should be InputProvider, but this leads to circular import
    input_provider: ImportString[Any] | None = Field(default=None, title="Input provider")
    """Provider for human-input-handling."""

    event_handlers: list[EventHandlerConfig] = Field(
        default_factory=list,
        title="Event handlers",
        examples=[[StdoutEventHandlerConfig(handler="simple")]],
    )
    """Event handlers for processing agent stream events.

    Supports:
    - builtin: Simple/detailed console output
    - tts: Text-to-speech synthesis
    - callback: Custom handler via import path
    """

    def get_event_handlers(self) -> list[IndividualEventHandler]:
        """Get resolved event handlers from configuration.

        Returns:
            List of event handler callables.
        """
        from agentpool_config.event_handlers import resolve_handler_configs

        return resolve_handler_configs(self.event_handlers)

    def get_mcp_servers(self) -> list[MCPServerConfig]:
        """Get processed MCP server configurations.

        Converts string entries via ``BaseMCPServerConfig.from_string``: URLs become
        HTTP / SSE servers, everything else a stdio command line.

        Returns:
            List of MCPServerConfig instances

        Raises:
            ValueError: If string entry is empty
        """
        configs: list[MCPServerConfig] = []

        for server in self.mcp_servers:
            match server:
                case str():
                    if not server.strip():
                        raise ValueError("Empty MCP server command")
                    configs.append(BaseMCPServerConfig.from_string(server))
                case BaseMCPServerConfig():
                    configs.append(server)

        return configs


class BaseAgentConfig(NodeConfig):
    """Base configuration for agents."""

    requires_tool_confirmation: ToolConfirmationMode = Field(
        default="per_tool",
        examples=["always", "never", "per_tool"],
        title="Tool confirmation mode",
    )
    """How to handle tool confirmation:
    - "always": Always require confirmation for all tools
    - "never": Never require confirmation (ignore tool settings)
    - "per_tool": Use individual tool settings
    """

    hooks: HooksConfig = Field(default_factory=HooksConfig, title="Lifecycle hooks")
    """Hooks for intercepting and customizing agent behavior at key lifecycle points.

    Allows adding context, blocking operations, modifying inputs, or triggering
    side effects during run execution and tool usage.
    """

    environment: Annotated[
        ExecutionEnvironmentConfig | str | None,
        Field(
            default=None,
            title="Execution Environment",
            examples=["docker", E2bExecutionEnvironmentConfig(template="python-sandbox")],
        ),
    ] = None
    """Execution environment config for the agent's own toolsets."""

    def get_execution_environment(self) -> ExecutionEnvironment:
        """Get the execution environment for this agent."""
        from exxec.local_provider import LocalExecutionEnvironment
        from exxec_config import BaseExecutionEnvironmentConfig

        match self.environment:
            case BaseExecutionEnvironmentConfig() as cfg:
                return cfg.get_provider()
            case str() | None:
                return LocalExecutionEnvironment(cwd=self.environment)
            case _ as unreachable:
                assert_never(unreachable)
