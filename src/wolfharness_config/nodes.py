"""Team configuration models."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated, Any, Literal, assert_never

from evented_config import EventConfig, FileWatchConfig, TimeEventConfig
from exxec_config import E2bExecutionEnvironmentConfig, ExecutionEnvironmentConfig
from pydantic import ConfigDict, Field, HttpUrl, ImportString, field_validator
from schemez import Schema

from wolfharness_config.capabilities import CapabilityConfig
from wolfharness_config.event_handlers import EventHandlerConfig, StdoutEventHandlerConfig
from wolfharness_config.forward_targets import (
    FileConnectionConfig,
    ForwardingTarget,
    NodeConnectionConfig,
)
from wolfharness_config.hooks import HooksConfig
from wolfharness_config.lifecycle import LifecycleConfig
from wolfharness_config.mcp_server import (
    BaseMCPServerConfig,
    MCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)
from wolfharness_config.team_mode import TeamModeConfig


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

    from wolfharness.common_types import IndividualEventHandler


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

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        examples=[{"use_session_pool": True}],
        title="Node metadata",
    )
    """Arbitrary metadata for the node.

    Can be used for feature flags, annotations, or other protocol-specific
    configuration that does not fit into structured fields.
    """

    triggers: list[EventConfig] = Field(
        default_factory=list,
        examples=[
            [
                TimeEventConfig(
                    name="daily_check",
                    schedule="0 9 * * *",
                    prompt="Daily status update",
                )
            ],
            [
                FileWatchConfig(
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
    - str entries are converted to StdioMCPServerConfig
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
        from wolfharness_config.event_handlers import resolve_handler_configs

        return resolve_handler_configs(self.event_handlers)

    def get_mcp_servers(self) -> list[MCPServerConfig]:
        """Get processed MCP server configurations.

        Converts string entries to StdioMCPServerConfigs by splitting
        into command and arguments.

        Returns:
            List of MCPServerConfig instances

        Raises:
            ValueError: If string entry is empty
        """
        configs: list[MCPServerConfig] = []

        for server in self.mcp_servers:
            match server:
                case str():
                    parts = server.split()
                    if not parts:
                        raise ValueError("Empty MCP server command")

                    configs.append(StdioMCPServerConfig(command=parts[0], args=parts[1:]))
                case BaseMCPServerConfig():
                    configs.append(server)

        return configs


class ResourceConfig(Schema):
    """Configuration for resource access tools.

    Controls whether the ``ResourceCapability`` (unified resource access
    via ``list_mcp_resources``, ``list_mcp_resource_templates``, and
    ``read_mcp_resource``) is automatically attached to the agent.

    Attributes:
        enabled: When ``True`` (default), the resource tools are available.
            Set to ``False`` to opt out.

    Example:
        ```yaml
        agents:
          my_agent:
            resources:
              enabled: false
        ```
    """

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "x-icon": "octicon:package-16",
            "x-doc-title": "Resource Configuration",
        },
    )

    enabled: bool = Field(
        default=True,
        title="Enable resource access tools",
    )
    """When ``True``, resource access tools are attached to the agent."""


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

    hooks: HooksConfig | None = Field(
        default=None,
        title="Lifecycle hooks",
    )
    """Hooks for intercepting and customizing agent behavior at key lifecycle points.

    Allows adding context, blocking operations, modifying inputs, or triggering
    side effects during run execution and tool usage.
    """

    metadata: dict[str, Any] = Field(default_factory=dict, title="Agent metadata")
    """Arbitrary metadata for the agent.

    Can be used for feature flags, annotations, and other per-agent
    configuration that doesn't fit into standard fields.

    Example:
        ```yaml
        metadata:
          use_session_pool: true
        ```
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

    capabilities: list[CapabilityConfig] = Field(
        default_factory=list,
        title="Agent capabilities",
    )
    """Pydantic-ai capabilities attached to this agent.

    Each entry is a capability config (built-in or generic import path).
    Built-in types: ``loop_detection``, ``token_budget``,
    ``tool_output_budget``, ``dcp``, ``skill_activation``,
    ``memory``.

    Example:
        ```yaml
        capabilities:
          - type: loop_detection
            max_depth: 10
          - type: token_budget
            max_tokens: 100000
        ```
    """

    team_mode: TeamModeConfig | None = Field(
        default=None,
        title="Team mode override",
    )
    """Per-agent team mode overlay.

    When non-None, merges with the global ``team_mode`` from the manifest
    via :func:`wolfharness_config.team_mode.resolve_team_mode` to produce
    the effective team mode config for this agent.
    """

    elicitation_timeout: timedelta | None = Field(
        default=None,
        title="Elicitation timeout",
        examples=["300s", "5m", "10m"],
    )
    """How long to wait for user elicitation responses before aborting the run.

    Accepts time strings (``"5m"``, ``"300s"``), numbers (seconds), or
    ``timedelta``. Set to ``null`` (the default) for no timeout (infinite
    wait). Configure explicitly to enable a timeout.

    Example:
        ```yaml
        agents:
          my_agent:
            elicitation_timeout: 600s
        ```
    """

    lifecycle: LifecycleConfig | None = Field(
        default=None,
        title="Lifecycle configuration",
    )
    """Configuration for the RunLoop lifecycle dimensions.

    Controls storage backends (memory vs durable) and crash recovery
    strategy for the agent's RunLoop. When ``None``, all defaults
    (in-memory) are used.

    Example:
        ```yaml
        agents:
          my_agent:
            lifecycle:
              journal: durable
              snapshot: durable
              recover_strategy: retry
        ```
    """

    resources: ResourceConfig = Field(
        default_factory=ResourceConfig,
        title="Resource access configuration",
    )
    """Configuration for unified resource access tools.

    When ``enabled`` is ``True`` (default), the ``ResourceCapability``
    providing ``list_mcp_resources``, ``list_mcp_resource_templates``, and
    ``read_mcp_resource`` tools is automatically attached to the agent.

    Set ``enabled: false`` to opt out:

    Example:
        ```yaml
        agents:
          my_agent:
            resources:
              enabled: false
        ```
    """

    @field_validator("elicitation_timeout", mode="before")
    @classmethod
    def parse_elicitation_timeout(cls, v: str | timedelta | float | None) -> timedelta | None:
        """Parse string/number timeout to timedelta.

        Args:
            v: Raw value from YAML — string (``"5m"``), number (seconds),
                timedelta, or None.

        Returns:
            Parsed timedelta, or None for infinite wait.
        """
        if v is None:
            return None
        if isinstance(v, timedelta):
            return v
        if isinstance(v, int | float):
            return timedelta(seconds=v)
        # Parse string like "5m", "1h", "300s" — simple inline parser
        # to avoid importing from wolfharness (layer separation).
        import re

        pattern = re.compile(
            r"\s*(?P<weeks>[\d.]+)\s*w(?:ks?|eeks?)?"
            r"|(?P<days>[\d.]+)\s*d(?:ys?|ays?)?"
            r"|(?P<hours>[\d.]+)\s*h(?:rs?|ours?)?"
            r"|(?P<mins>[\d.]+)\s*m(?:ins?|inutes?)?"
            r"|(?P<secs>[\d.]+)\s*s(?:ecs?|econds?)?",
            re.IGNORECASE,
        )
        matches = pattern.findall(v)
        if not matches:
            raise ValueError(f"Invalid time format: {v}")
        multipliers = {
            "weeks": 60 * 60 * 24 * 7,
            "days": 60 * 60 * 24,
            "hours": 60 * 60,
            "mins": 60,
            "secs": 1,
        }
        total = 0.0
        for match in matches:
            for unit, val in zip(multipliers, match, strict=False):
                if val:
                    total += multipliers[unit] * float(val)
        if total <= 0:
            raise ValueError(f"Invalid time format: {v}")
        return timedelta(seconds=total)

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
