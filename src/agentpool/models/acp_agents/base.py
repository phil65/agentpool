"""Configuration models for ACP (Agent Client Protocol) agents."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import TYPE_CHECKING, Annotated, Any, Literal, assert_never

from exxec_config import (
    E2bExecutionEnvironmentConfig,
    ExecutionEnvironmentConfig,
    ExecutionEnvironmentStr,
)
from pydantic import ConfigDict, Field

from agentpool.models.fields import EnvVarsField  # noqa: TC001
from agentpool_config import AnyToolConfig, BaseToolConfig
from agentpool_config.nodes import BaseAgentConfig
from agentpool_config.toolsets import BaseToolsetConfig


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

    from agentpool.agents.acp_agent import ACPAgent
    from agentpool.common_types import AnyEventHandlerType
    from agentpool.delegation import AgentPool
    from agentpool.resource_providers import ResourceProvider
    from agentpool.tools.base import Tool
    from agentpool.ui.base import InputProvider


class BaseACPAgentConfig(BaseAgentConfig):
    """Base configuration for all ACP agents.

    Provides common fields and the interface for building commands.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:terminal-16",
            "x-doc-title": "ACP Agent Configuration",
        }
    )

    type: Literal["acp"] = Field("acp", init=False)
    """Top-level discriminator for agent type."""

    cwd: str | None = Field(
        default=None,
        title="Working Directory",
        examples=["/path/to/project", ".", "/home/user/myproject"],
    )
    """Working directory for the session."""

    env_vars: EnvVarsField = Field(default_factory=dict)
    """Environment variables to set."""

    tools: Sequence[AnyToolConfig | str] = Field(
        default_factory=list,
        title="Tools",
        examples=[
            [
                {"type": "subagent"},
                {"type": "agent_management"},
                "webbrowser:open",
            ],
        ],
    )
    """Tools and toolsets to expose to this ACP agent via MCP bridge.

    Supports both single tools and toolsets. These will be started as an
    in-process MCP server and made available to the external ACP agent.
    """

    client_execution_environment: Annotated[
        ExecutionEnvironmentStr | ExecutionEnvironmentConfig | None,
        Field(
            default=None,
            title="Client Execution Environment",
            examples=[
                "local",
                "docker",
                E2bExecutionEnvironmentConfig(template="python-sandbox"),
            ],
        ),
    ] = None
    """Execution environment for handling subprocess requests (filesystem, terminals).

    When the ACP subprocess requests file/terminal operations, this environment
    determines where those operations execute. Falls back to execution_environment
    if not set.

    Use cases:
    - None (default): Use same env as toolsets (execution_environment)
    - "local": Subprocess operates on its own local filesystem
    - Remote config: Subprocess operates in a specific remote environment
    """

    allow_file_operations: bool = Field(default=True, title="Allow File Operations")
    """Whether to allow file read/write operations."""

    allow_terminal: bool = Field(default=True, title="Allow Terminal")
    """Whether to allow terminal operations."""

    auto_approve: bool = Field(default=False, title="Auto-approve permissions")
    """If True, automatically approve all permission requests from the remote agent."""

    def get_command(self) -> str | None:
        """Get the command to spawn the ACP server.

        Returns None for registry-based agents (resolved at runtime).
        """
        return None

    def get_args(self) -> list[str]:
        """Get command arguments / extra args for registry agents."""
        return []

    def get_registry_id(self) -> str | None:
        """Get the ACP registry agent ID, if this is a registry-based agent."""
        return None

    def get_url(self) -> str | None:
        """Get the endpoint of a remote agent (None for agents run as a subprocess)."""
        return None

    def get_headers(self) -> dict[str, str]:
        """Get the HTTP headers for a remote agent."""
        return {}

    def get_tool_providers(self) -> list[ResourceProvider]:
        """Get all resource providers for this agent's tools."""
        from agentpool.resource_providers import StaticResourceProvider
        from agentpool.tools.base import FunctionTool

        providers: list[ResourceProvider] = []
        static_tools: list[Tool] = []

        for tool_config in self.tools:
            try:
                match tool_config:
                    case BaseToolsetConfig():
                        providers.append(tool_config.get_provider())
                    case str():
                        static_tools.append(FunctionTool.from_callable(tool_config))
                    case BaseToolConfig():
                        static_tools.append(tool_config.get_tool())
                    case _ as unreachable:
                        assert_never(unreachable)
            except Exception:  # noqa: BLE001
                continue

        if static_tools:
            providers.append(StaticResourceProvider(name="tools", tools=static_tools))

        return providers

    def get_agent[TDeps](
        self,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,  # type: ignore[valid-type]
    ) -> ACPAgent[TDeps]:
        from agentpool.agents.acp_agent import ACPAgent

        return ACPAgent[TDeps].from_config(
            self,
            event_handlers=event_handlers,
            input_provider=input_provider,
            agent_pool=pool,
            deps_type=deps_type,
        )

    def get_client_execution_environment(self) -> ExecutionEnvironment | None:
        """Create client execution environment from config.

        Returns None if not configured (caller should fall back to main env).
        """
        from exxec import get_environment

        if self.client_execution_environment is None:
            return None
        if isinstance(self.client_execution_environment, str):
            return get_environment(self.client_execution_environment)
        return self.client_execution_environment.get_provider()


class ACPAgentConfig(BaseACPAgentConfig):
    """Configuration for a custom ACP agent with explicit command.

    Use this for ACP servers that don't have a preset, or when you need
    full control over the command and arguments.

    Example:
        ```yaml
        agents:
          custom_agent:
            type: acp
            provider: custom
            command: my-acp-server
            args: ["--mode", "coding"]
            cwd: /path/to/project
        ```
    """

    model_config = ConfigDict(json_schema_extra={"title": "Custom ACP Agent Configuration"})

    provider: Literal["custom"] = Field("custom", init=False)
    """Discriminator for custom ACP agent."""

    command: str = Field(
        ...,
        title="Command",
        examples=["claude-agent-acp", "aider", "my-custom-acp"],
    )
    """Command to spawn the ACP server."""

    args: list[str] = Field(
        default_factory=list,
        title="Arguments",
        examples=[["--mode", "coding"], ["--debug", "--verbose"]],
    )
    """Arguments to pass to the command."""

    def get_command(self) -> str:
        """Get the command to spawn the ACP server."""
        return self.command

    def get_args(self) -> list[str]:
        """Get command arguments."""
        return self.args


class RegistryACPAgentConfig(BaseACPAgentConfig):
    """Configuration for an ACP agent resolved from the ACP registry.

    The agent binary/package is automatically installed and launched based on
    the registry entry. Supports uvx, npx, and binary distributions.

    Example:
        ```yaml
        agents:
          coder:
            type: acp
            provider: registry
            registry_id: goose
            extra_args: ["--model", "gpt-4o"]
            cwd: /path/to/project

          gemini:
            type: acp
            provider: registry
            registry_id: gemini
            extra_args: ["--experimental-acp", "--model", "gemini-2.5-pro"]
        ```
    """

    model_config = ConfigDict(json_schema_extra={"title": "Registry ACP Agent Configuration"})

    provider: Literal["registry"] = Field("registry", init=False)
    """Discriminator for registry-based ACP agent."""

    registry_id: str = Field(
        ...,
        title="Registry Agent ID",
        examples=["goose", "gemini", "claude-acp", "opencode", "codex-acp", "stakpak"],
    )
    """Agent ID in the ACP registry (https://agentclientprotocol.com)."""

    extra_args: list[str] = Field(
        default_factory=list,
        title="Extra Arguments",
        examples=[
            ["--model", "gpt-4o"],
            ["--experimental-acp", "--yolo"],
        ],
    )
    """Extra CLI arguments appended after the registry-defined command."""

    def get_registry_id(self) -> str:
        """Get the ACP registry agent ID."""
        return self.registry_id

    def get_args(self) -> list[str]:
        """Get extra arguments for the registry agent."""
        return self.extra_args


class RemoteACPAgentConfig(BaseACPAgentConfig):
    """Configuration for a remote ACP agent reached over Streamable HTTP (experimental).

    Uses the client side of ACP's Streamable HTTP transport RFD, compatible with the
    TypeScript SDK's experimental server. Nothing is spawned locally; file and terminal
    requests of the remote agent are still served by this client.

    Example:
        ```yaml
        agents:
          contexter:
            type: acp
            provider: remote
            url: https://contexter.net/api/acp
            headers:
              X-API-Key: ${CONTEXTER_API_KEY}
        ```
    """

    model_config = ConfigDict(json_schema_extra={"title": "Remote ACP Agent Configuration"})

    provider: Literal["remote"] = Field("remote", init=False)
    """Discriminator for remote ACP agent."""

    url: str = Field(
        ...,
        title="Endpoint URL",
        examples=["https://example.com/api/acp", "http://localhost:3000/api/acp"],
    )
    """The agent's ``/acp`` endpoint."""

    headers: dict[str, str] = Field(
        default_factory=dict,
        title="HTTP Headers",
        examples=[{"X-API-Key": "${MY_API_KEY}"}, {"Authorization": "Bearer ${MY_TOKEN}"}],
    )
    """Headers sent with every request (e.g. authentication).

    ``${VAR}`` in values is replaced by the environment variable when the agent starts.
    """

    def get_url(self) -> str:
        """Get the remote endpoint."""
        return self.url

    def get_headers(self) -> dict[str, str]:
        """Get the HTTP headers, with ``${VAR}`` references resolved.

        Raises:
            ValueError: A header references an unset environment variable.
        """
        import os
        import string

        resolved: dict[str, str] = {}
        for name, value in self.headers.items():
            try:
                resolved[name] = string.Template(value).substitute(os.environ)
            except KeyError as exc:
                msg = f"Header {name!r} references unset environment variable {exc.args[0]}"
                raise ValueError(msg) from exc
        return resolved
