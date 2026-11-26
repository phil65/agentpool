"""Pool server configuration."""

from datetime import timedelta
from typing import Annotated, Literal, assert_never

from pydantic import ConfigDict, Field, SecretStr, model_validator
from schemez import Schema


TransportType = Literal["stdio", "sse", "streamable-http"]


class BasePoolServerConfig(Schema):
    """Base configuration for pool servers."""

    type: str = Field(title="Server type")
    """Type discriminator for server configurations."""

    enabled: bool = Field(default=False, title="Server enabled")
    """Whether this server is currently enabled."""


class MCPTokenAuthConfig(Schema):
    """Clients authenticate with a token of their own (``Authorization: Bearer <token>``).

    For a server on a trusted network or behind a proxy: no sign-in, no expiry. Each
    client gets its own token, so one can be revoked by removing it.
    """

    type: Literal["tokens"] = Field("tokens", init=False)
    """Auth type discriminator."""

    tokens: dict[str, SecretStr] = Field(
        min_length=1,
        title="Client tokens",
        examples=[{"zed": "${ZED_MCP_TOKEN}", "ci": "${CI_MCP_TOKEN}"}],
    )
    """Token of each client, by the client's name (shown as its client ID)."""

    model_config = ConfigDict(frozen=True)


class MCPJWTAuthConfig(Schema):
    """Clients present JWTs an identity provider issued (OAuth 2.1 resource server).

    Tokens are checked against the provider's keys (``jwks_uri``, or one ``public_key``),
    and their issuer, audience and scopes. With ``authorization_servers``, the server
    publishes OAuth protected resource metadata (RFC 9728), so MCP clients find where to
    sign in by themselves.
    """

    type: Literal["jwt"] = Field("jwt", init=False)
    """Auth type discriminator."""

    jwks_uri: str | None = Field(
        default=None,
        title="JWKS URI",
        examples=["https://auth.example.com/.well-known/jwks.json"],
    )
    """Where the provider publishes its signing keys (rotated keys are picked up)."""

    public_key: str | None = Field(default=None, title="Public key")
    """A PEM public key (or a symmetric secret for HS algorithms) instead of ``jwks_uri``."""

    issuer: str | None = Field(default=None, title="Issuer", examples=["https://auth.example.com"])
    """The ``iss`` tokens must carry."""

    audience: str | None = Field(
        default=None, title="Audience", examples=["https://mcp.example.com/mcp"]
    )
    """The ``aud`` tokens must carry: this server, so tokens for others are refused."""

    algorithm: str | None = Field(default=None, title="Algorithm", examples=["RS256"])
    """The signing algorithm (default: the key's, RS256 for a JWKS)."""

    required_scopes: list[str] = Field(
        default_factory=list, title="Required scopes", examples=[["mcp:agents"]]
    )
    """Scopes every token must have."""

    authorization_servers: list[str] = Field(
        default_factory=list,
        title="Authorization servers",
        examples=[["https://auth.example.com"]],
    )
    """Authorization servers to advertise to clients (needs ``base_url``)."""

    base_url: str | None = Field(
        default=None, title="Public base URL", examples=["https://mcp.example.com"]
    )
    """This server's public URL, for the advertised metadata."""

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def _check(self) -> MCPJWTAuthConfig:
        if (self.jwks_uri is None) == (self.public_key is None):
            msg = "Set exactly one of jwks_uri and public_key"
            raise ValueError(msg)
        if self.authorization_servers and self.base_url is None:
            msg = "authorization_servers needs base_url (this server's public URL)"
            raise ValueError(msg)
        return self


MCPAuthConfig = Annotated[MCPTokenAuthConfig | MCPJWTAuthConfig, Field(discriminator="type")]
"""How clients of serve-mcp over HTTP authenticate."""


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
    """Which prompts of the pool's prompt library to serve as MCP prompts:
    - True: All (``prompts.system_prompts`` by name, prompt hub prompts as
      ``<provider>.<name>``)
    - False: None
    - list[str]: These names
    """

    serve_skills: bool = Field(default=True, title="Serve skills")
    """Publish the pool's skills through the MCP Skills extension (SEP-2640)."""

    serve_events: bool = Field(default=False, title="Serve events")
    """Publish the pool's session activity as MCP events (Events extension, SEP-3415).

    ``session.state_update`` (a session's turn runs, waits for the user, or stopped),
    ``session.info_update`` (a session was created or got its title),
    ``session.usage_update`` (context fill and cost), ``session.notice`` and
    ``session.compaction_update``, as ACP v2 reports them. Clients subscribe with the
    optional arguments ``agent`` and ``session_id``.
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
        default_factory=list,
        title="CORS origins",
        examples=[["http://localhost:6274"], ["https://app.example.com"]],
    )
    """Origins of browser clients allowed to call the server (SSE / Streamable-HTTP only).

    Each gets CORS headers and passes the server's origin check. Empty (the default):
    no cross-origin browser access. ``"*"`` lets any website open in a browser on the
    machine call the pool's agents.
    """

    auth: MCPAuthConfig | None = Field(default=None, title="Authentication")
    """How clients authenticate (SSE / Streamable-HTTP only). Unset: anyone who can reach
    the server's address can use the pool's agents, so keep it on localhost then."""

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def _check_auth(self) -> MCPPoolServerConfig:
        if self.auth is not None and self.transport == "stdio":
            msg = "auth applies to HTTP transports (sse, streamable-http), not stdio"
            raise ValueError(msg)
        return self

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


PoolServerConfig = Annotated[
    MCPPoolServerConfig | A2APoolServerConfig | ACPPoolServerConfig | AGUIPoolServerConfig,
    Field(discriminator="type"),
]
