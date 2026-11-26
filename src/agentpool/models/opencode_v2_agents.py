"""Configuration models for OpenCode V2 agents."""

from typing import TYPE_CHECKING, Any, Literal, assert_never

from pydantic import ConfigDict, Field
from schemez import Schema

from agentpool.models.agents import AnyToolConfig  # noqa: TC001
from agentpool.models.fields import EnvVarsField  # noqa: TC001
from agentpool_config.nodes import BaseAgentConfig


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.agents.opencode_v2_agent import OpenCodeV2Agent
    from agentpool.common_types import AnyEventHandlerType
    from agentpool.delegation import AgentPool
    from agentpool.resource_providers import ResourceProvider
    from agentpool.tools.base import Tool
    from agentpool.ui.base import InputProvider


type OpenCodePermissionMode = Literal["ask", "allow", "deny"]
"""How permission requests from an OpenCode V2 session are handled."""

type OpenCodePermissionEffect = Literal["allow", "deny", "ask"]
"""Effect of an explicit OpenCode V2 permission rule."""


class OpenCodePermissionRuleConfig(Schema):
    """One explicit OpenCode V2 permission rule."""

    action: str = Field(examples=["bash", "edit", "*"])
    """Action pattern (glob matched, e.g. ``bash``, ``edit``, ``*``)."""

    resource: str = Field(default="*", examples=["*", "/workspace/**"])
    """Resource pattern (glob matched)."""

    effect: OpenCodePermissionEffect = "allow"
    """Outcome when this rule matches (``allow``, ``deny``, or ``ask``)."""


class OpenCodeV2AgentConfig(BaseAgentConfig):
    """Configuration for OpenCode V2 (v2.3+) agents.

    Talks to an OpenCode V2 HTTP/SSE server (``opencode serve`` or any compatible
    OpenCode V2 endpoint such as ``agentpool serve-opencode``). When ``url`` is
    omitted, AgentPool spawns and manages a private ``opencode serve`` process
    automatically.

    Example:
        ```yaml
        agents:
          opencode_local:
            type: opencode_v2
            model: anthropic/claude-sonnet-4-5
            variant: high
            permission_mode: ask
            tools:
              - type: agent_management

          opencode_remote:
            type: opencode_v2
            url: http://127.0.0.1:4096
            password: ${OPENCODE_PASSWORD}
            agent: build
            permission_mode: allow
        ```
    """

    model_config = ConfigDict(
        json_schema_extra={
            "title": "OpenCode V2 Agent Configuration",
            "x-icon": "simple-icons:opencode",
        }
    )

    type: Literal["opencode_v2"] = Field(default="opencode_v2", init=False)
    """Top-level discriminator for agent type."""

    url: str | None = Field(default=None, title="Server URL", examples=["http://127.0.0.1:4096"])
    """Base URL of an existing OpenCode V2 server.

    When omitted, a private ``opencode serve`` subprocess is spawned on context entry
    and terminated on exit.
    """

    password: str | None = Field(
        default=None,
        title="Server Password",
        examples=["${OPENCODE_PASSWORD}"],
    )
    """Basic-auth password for ``url`` (``${VAR}`` references are expanded).

    Falls back to ``OPENCODE_PASSWORD`` / ``OPENCODE_SERVER_PASSWORD`` when connecting
    to an external ``url``. Managed subprocesses generate an ephemeral password automatically.
    """

    command: str = Field(default="opencode", title="OpenCode Executable")
    """Executable name or path used to launch ``opencode serve`` when ``url`` is not set."""

    startup_timeout: float = Field(default=15.0, gt=0, title="Startup Timeout (s)")
    """Seconds to wait for a managed ``opencode serve`` process to become ready."""

    headers: dict[str, str] = Field(default_factory=dict, title="HTTP Headers")
    """Additional HTTP headers sent with every API request (``${VAR}`` expanded)."""

    env_vars: EnvVarsField = Field(default_factory=dict)
    """Extra environment variables passed to a managed ``opencode serve`` subprocess."""

    agent: str | None = Field(default=None, title="OpenCode Agent", examples=["build", "plan"])
    """OpenCode agent ID to select on the session (uses the server default when omitted)."""

    model: str | None = Field(
        default=None,
        title="Model",
        examples=["anthropic/claude-sonnet-4-5", "openai/gpt-5"],
    )
    """Model to use, as ``provider/model`` or a bare model ID (resolved via ``/api/model``)."""

    variant: str | None = Field(default=None, title="Model Variant", examples=["medium", "high"])
    """Optional model variant (e.g. reasoning effort level supported by the model)."""

    permission_mode: OpenCodePermissionMode = Field(
        default="allow",
        title="Permission Mode",
        examples=["allow", "ask", "deny"],
    )
    """How permission requests are handled when ``permissions`` is not explicitly set.

    - ``allow`` (default): Auto-approve tool permissions for programmatic use.
    - ``ask``: Ask AgentPool's ``InputProvider`` for each permission request.
    - ``deny``: Reject tool permission requests.
    """

    permissions: list[OpenCodePermissionRuleConfig] | None = Field(
        default=None,
        title="Permission Rules",
    )
    """Optional explicit OpenCode V2 permission rules applied to created sessions."""

    tools: list[AnyToolConfig | str] = Field(
        default_factory=list,
        title="Tools",
        examples=[[{"type": "agent_management"}, "webbrowser:open"]],
    )
    """AgentPool tools and toolsets to expose to OpenCode V2 via an in-process MCP bridge."""

    def get_password(self) -> str | None:
        """Return ``password`` with ``${VAR}`` references expanded."""
        from agentpool.utils.env_refs import expand_env_refs

        if self.password is None:
            return None
        return expand_env_refs({"password": self.password})["password"]

    def get_headers(self) -> dict[str, str]:
        """Return ``headers`` with ``${VAR}`` references expanded."""
        from agentpool.utils.env_refs import expand_env_refs

        return expand_env_refs(self.headers)

    def get_env_vars(self) -> dict[str, str]:
        """Return ``env_vars`` with ``${VAR}`` references expanded."""
        from agentpool.utils.env_refs import expand_env_refs

        return expand_env_refs(self.env_vars)

    def get_tool_providers(self) -> list[ResourceProvider]:
        """Resolve configured tools and toolsets into ResourceProvider instances."""
        from agentpool import log
        from agentpool.resource_providers import StaticResourceProvider
        from agentpool.tools.base import FunctionTool
        from agentpool_config import BaseToolConfig
        from agentpool_config.toolsets import BaseToolsetConfig

        logger = log.get_logger(__name__)
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
            except Exception:
                logger.exception("Failed to load tool", config=tool_config)
                continue

        if static_tools:
            providers.append(StaticResourceProvider(tools=static_tools))

        return providers

    def get_agent[TDeps](
        self,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,  # type: ignore[valid-type]
    ) -> OpenCodeV2Agent[TDeps]:
        from agentpool.agents.opencode_v2_agent import OpenCodeV2Agent

        return OpenCodeV2Agent[TDeps].from_config(
            self,
            event_handlers=event_handlers,
            input_provider=input_provider,
            agent_pool=pool,
            deps_type=deps_type,
        )
