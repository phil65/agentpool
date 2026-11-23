"""Pool server configuration."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal, assert_never

from pydantic import ConfigDict, Field, SecretStr
from schemez import Schema


TransportType = Literal["stdio", "sse", "streamable-http"]


class BasePoolServerConfig(Schema):
    """Base configuration for pool servers."""

    type: str = Field(title="Server type")
    """Type discriminator for server configurations."""

    enabled: bool = Field(default=False, title="Server enabled")
    """Whether this server is currently enabled."""


class MCPPoolServerConfig(BasePoolServerConfig):
    """Configuration for pool-based MCP server."""

    type: Literal["mcp"] = Field("mcp", init=False)
    """MCP server type."""

    # Resource exposure control
    serve_nodes: list[str] | bool = Field(
        default=True,
        title="Serve nodes",
        examples=[["node1", "node2"], ["analysis", "transform"]],
    )
    """Which nodes to expose as tools:
    - True: All nodes
    - False: No nodes
    - list[str]: Specific node names
    """

    serve_prompts: list[str] | bool = Field(
        default=True,
        title="Serve prompts",
        examples=[["prompt1", "prompt2"], ["system", "user"]],
    )
    """Which prompts to expose:
    - True: All prompts from manifest
    - False: No prompts
    - list[str]: Specific prompt names
    """

    serve_skills: bool = Field(default=True, title="Serve skills")
    """Publish the pool's skills through the MCP Skills extension (SEP-2640)."""

    serve_events: bool = Field(default=False, title="Serve events")
    """Publish the pool's session activity as MCP events (Events extension, SEP-3415).

    ``session.state_update`` (a session's turn runs, waits for the user, or stopped) and
    ``session.info_update`` (a session was created or got its title), as ACP v2 reports
    them. Clients subscribe with the optional arguments ``agent`` and ``session_id``.
    Experimental, as the extension is.
    """

    background_tasks: bool = Field(default=False, title="Background tasks")
    """Let clients run agent and team tools as MCP background tasks (SEP-2663).

    Needs the ``mcp-tasks`` extra. A client on the ``2026-07-28`` protocol that opts in gets
    a task ID right away and polls for the result, so long runs survive request timeouts
    and idle HTTP sessions. Every other client calls the tools synchronously, as before.
    """

    tasks_url: str | None = Field(
        default=None,
        title="Tasks backend URL",
        examples=["memory://", "redis://localhost:6379/0"],
    )
    """Where task state lives (only with ``background_tasks``).

    ``memory://`` keeps tasks in this process and loses them on restart; a Redis or Valkey
    URL keeps them across restarts. Unset uses ``FASTMCP_DOCKET_URL``, else ``memory://``.
    Tasks always run in this process's own worker, which holds the pool.
    """

    tasks_ttl: timedelta | None = Field(
        default=None,
        title="Task lifetime",
        examples=["PT2H", 3600],
    )
    """How long a task's state and result are kept (only with ``background_tasks``).

    Counted from when the task starts, so it must cover the run plus the time until the client
    fetches the result. Unset uses ``FASTMCP_DOCKET_EXECUTION_TTL``, else 15 minutes.
    """

    transport: TransportType = Field(
        default="stdio",
        title="Transport type",
        examples=["stdio", "sse", "streamable-http"],
    )
    """Transport type to use."""

    host: str = Field(
        default="localhost",
        title="Server host",
        examples=["localhost", "0.0.0.0", "127.0.0.1"],
    )
    """Host to bind server to (SSE / Streamable-HTTP only)."""

    port: int = Field(
        default=3001,
        gt=0,
        title="Server port",
        examples=[3001, 8080, 9000],
    )
    """Port to listen on (SSE / Streamable-HTTP only)."""

    cors_origins: list[str] = Field(
        default_factory=lambda: ["*"],
        title="CORS origins",
        examples=[["*"], ["https://example.com", "https://app.com"]],
    )
    """Allowed CORS origins (SSE / Streamable-HTTP only)."""

    model_config = ConfigDict(frozen=True)

    def should_serve_node(self, name: str) -> bool:
        """Check if a node should be exposed."""
        match self.serve_nodes:
            case True:
                return True
            case False:
                return False
            case list():
                return name in self.serve_nodes
            case _ as unreachable:
                assert_never(unreachable)

    def should_serve_prompt(self, name: str) -> bool:
        """Check if a prompt should be exposed."""
        match self.serve_prompts:
            case True:
                return True
            case False:
                return False
            case list():
                return name in self.serve_prompts
            case _ as unreachable:
                assert_never(unreachable)


class A2APoolServerConfig(BasePoolServerConfig):
    """Configuration for A2A (Agent-to-Agent) server."""

    type: Literal["a2a"] = Field("a2a", init=False)
    """A2A server type."""

    host: str = Field(
        default="localhost",
        title="Server host",
        examples=["localhost", "0.0.0.0", "127.0.0.1"],
    )
    """Host to bind server to."""

    port: int = Field(
        default=8001,
        gt=0,
        title="Server port",
        examples=[8001, 8080, 9000],
    )
    """Port to listen on."""

    raise_exceptions: bool = Field(
        default=False,
        title="Raise exceptions",
    )
    """Whether to raise exceptions during server start."""

    cors_origins: list[str] = Field(
        default_factory=lambda: ["*"],
        title="CORS origins",
        examples=[["*"], ["https://example.com", "https://app.com"]],
    )
    """Allowed CORS origins."""

    serve_docs: bool = Field(default=True, title="Serve documentation")
    """Whether to serve API documentation endpoints."""


class ACPPoolServerConfig(BasePoolServerConfig):
    """Configuration for ACP (Agent Client Protocol) server."""

    type: Literal["acp"] = Field("acp", init=False)
    """ACP server type."""

    file_access: bool = Field(
        default=True,
        title="File access",
    )
    """Whether to allow file system access."""

    terminal_access: bool = Field(
        default=True,
        title="Terminal access",
    )
    """Whether to allow terminal/shell access."""

    raise_exceptions: bool = Field(
        default=False,
        title="Raise exceptions",
    )
    """Whether to raise exceptions during server start."""


class AGUIPoolServerConfig(BasePoolServerConfig):
    """Configuration for AGUI (AG-UI) server."""

    type: Literal["agui"] = Field("agui", init=False)
    """AGUI server type."""

    host: str = Field(
        default="localhost",
        title="Server host",
        examples=["localhost", "0.0.0.0", "127.0.0.1"],
    )
    """Host to bind server to."""

    port: int = Field(
        default=8002,
        gt=0,
        title="Server port",
        examples=[8002, 8080, 9000],
    )
    """Port to listen on."""

    raise_exceptions: bool = Field(
        default=False,
        title="Raise exceptions",
    )
    """Whether to raise exceptions during server start."""

    cors_origins: list[str] = Field(
        default_factory=lambda: ["*"],
        title="CORS origins",
        examples=[["*"], ["https://example.com", "https://app.com"]],
    )
    """Allowed CORS origins."""

    serve_docs: bool = Field(default=True, title="Serve documentation")
    """Whether to serve API documentation endpoints."""


class OpenAIAPIPoolServerConfig(BasePoolServerConfig):
    """Configuration for OpenAI-compatible API server."""

    type: Literal["openai-api"] = Field("openai-api", init=False)
    """OpenAI API server type."""

    host: str = Field(
        default="0.0.0.0",
        title="Server host",
        examples=["localhost", "0.0.0.0", "127.0.0.1"],
    )
    """Host to bind server to."""

    port: int = Field(
        default=8000,
        gt=0,
        title="Server port",
        examples=[8000, 8080, 9000],
    )
    """Port to listen on."""

    cors: bool = Field(default=True, title="Enable CORS")
    """Whether to enable CORS middleware."""

    docs: bool = Field(default=True, title="Enable API docs")
    """Whether to enable API documentation endpoints."""

    api_key: SecretStr | None = Field(
        default=None,
        title="API key",
    )
    """Optional API key for authentication."""

    raise_exceptions: bool = Field(
        default=False,
        title="Raise exceptions",
    )
    """Whether to raise exceptions during server start."""

    cors_origins: list[str] = Field(
        default_factory=lambda: ["*"],
        title="CORS origins",
        examples=[["*"], ["https://example.com", "https://app.com"]],
    )
    """Allowed CORS origins."""


PoolServerConfig = Annotated[
    MCPPoolServerConfig
    | A2APoolServerConfig
    | ACPPoolServerConfig
    | AGUIPoolServerConfig
    | OpenAIAPIPoolServerConfig,
    Field(discriminator="type"),
]
