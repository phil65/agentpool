"""Configuration models for AG-UI agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field

from agentpool_config import ToolConfig  # noqa: TC001
from agentpool_config.nodes import BaseAgentConfig


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.agents.agui_agent import AGUIAgent
    from agentpool.common_types import AnyEventHandlerType
    from agentpool.delegation import AgentPool
    from agentpool.ui.base import InputProvider


class AGUIAgentConfig(BaseAgentConfig):
    """Configuration for AG-UI protocol agents.

    AG-UI agents connect to remote HTTP endpoints that implement the AG-UI protocol,
    enabling integration of any AG-UI compatible server into the agentpool pool.

    Example:
        ```yaml
        agents:
          remote_assistant:
            type: agui
            endpoint: http://localhost:8000/agent/run
            timeout: 30.0
            headers:
              X-API-Key: ${API_KEY}
            tools:
              - import_path: mymodule.my_tool_function

          managed_agent:
            endpoint: http://localhost:8765/agent/run
            startup_command: "uv run ag-ui-server config.yml"
            tools:
              - import_path: package.tool_one
              - import_path: package.tool_two
        ```
    """

    model_config = ConfigDict(
        json_schema_extra={"title": "AG-UI Agent Configuration", "x-icon": "mdi:api"}
    )

    type: Literal["agui"] = Field(default="agui", init=False)
    """Top-level discriminator for agent type."""

    endpoint: str = Field(
        ...,
        examples=["http://localhost:8000/agent/run", "https://api.example.com/v1/agent/run"],
    )
    """HTTP endpoint for the AG-UI agent server."""

    timeout: float = Field(default=60.0, ge=0.1, examples=[30.0, 60.0, 120.0])
    """Request timeout in seconds."""

    headers: dict[str, str] = Field(default_factory=dict)
    """Additional HTTP headers to send with requests.

    Useful for authentication or custom routing.
    Environment variables can be used: ${VAR_NAME}
    """

    def get_headers(self) -> dict[str, str]:
        """Get the HTTP headers, with ``${VAR}`` references resolved.

        Raises:
            ValueError: A header references an unset environment variable.
        """
        from agentpool.utils.env_refs import expand_env_refs

        return expand_env_refs(self.headers)

    startup_command: str | None = Field(
        default=None,
        examples=[
            "uv run ag-ui-server config.yml",
            "python -m my_agui_server --port 8000",
        ],
    )
    """Optional shell command to start the AG-UI server automatically.

    When provided, the agent will spawn this command on context entry
    and terminate it on exit. Useful for testing or self-contained deployments.
    """

    startup_delay: float = Field(default=0.0, ge=0.0, examples=[0.0, 1.0])
    """Extra seconds to wait after starting the server.

    Usually not needed: the agent waits until the endpoint accepts connections
    (see ``startup_timeout``). Only relevant when startup_command is provided.
    """

    startup_timeout: float = Field(default=30.0, gt=0.0, examples=[30.0, 120.0])
    """Seconds to wait until the server accepts connections on the endpoint's host and port.

    Only relevant when startup_command is provided.
    """

    tools: list[ToolConfig] = Field(default_factory=list)
    """Tools to expose to the remote agent for client-side execution.

    When the remote AG-UI agent requests a tool call, these tools are executed
    locally and the result is sent back. This enables human-in-the-loop workflows
    and local capability exposure to remote agents.
    """

    def get_agent[TDeps](
        self,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,  # type: ignore[valid-type]
    ) -> AGUIAgent[TDeps]:
        from agentpool.agents.agui_agent import AGUIAgent

        return AGUIAgent[TDeps].from_config(
            self,
            event_handlers=event_handlers,
            input_provider=input_provider,
            agent_pool=pool,
            deps_type=deps_type,
        )
