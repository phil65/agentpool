"""MCP server configuration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from pydantic import ConfigDict, Field, HttpUrl, model_validator
from schemez import Schema


if TYPE_CHECKING:
    from pydantic_ai.mcp import MCPToolset


class MCPServerAuthSettings(Schema):
    """Represents authentication configuration for a server.

    Minimal OAuth v2.1 support with sensible defaults.
    """

    oauth: bool = Field(default=False, title="Enable OAuth")

    # Local callback server configuration
    redirect_port: int = Field(
        default=3030, ge=1, lt=65536, examples=[3030, 8080, 9000], title="Redirect port"
    )
    redirect_path: str = Field(
        default="/callback",
        examples=["/callback", "/auth/callback", "/oauth"],
        title="Redirect path",
    )

    # Optional scope override. If set to a list, values are space-joined.
    scope: str | list[str] | None = Field(
        default=None,
        examples=["read write", ["read", "write"], "admin"],
        title="OAuth scope",
    )

    # Token persistence: use OS keychain via 'keyring' by default; fallback to 'memory'.
    persist: Literal["keyring", "memory"] = Field(
        default="keyring",
        examples=["keyring", "memory"],
        title="Token persistence",
    )


class BaseMCPServerConfig(Schema):
    """Base model for MCP server configuration."""

    type: str = Field(title="Server type")
    """Type discriminator for MCP server configurations."""

    name: str | None = Field(
        default=None,
        examples=["my_server", "api_connector", "file_handler"],
        title="Server name",
    )
    """Optional name for referencing the server."""

    enabled: bool = Field(default=True, title="Server enabled")
    """Whether this server is currently enabled."""

    env: dict[str, str] | None = Field(default=None, title="Environment variables")
    """Environment variables to pass to the server process."""

    timeout: float = Field(
        default=60.0,
        gt=0,
        examples=[30.0, 60.0, 120.0],
        title="Server timeout",
    )
    """Timeout for the server process in seconds."""

    init_timeout: float = Field(
        default=30.0,
        gt=0,
        examples=[10.0, 30.0, 120.0],
        title="Startup timeout",
    )
    """Seconds to wait for the server to start and finish the MCP handshake.

    A server that takes longer is marked failed instead of blocking its agent or pool.
    """

    enabled_tools: list[str] | None = Field(
        default=None,
        examples=[["read_file", "list_directory"], ["search", "fetch"]],
        title="Enabled tools",
    )
    """If set, only these tools will be available (whitelist).
    Mutually exclusive with disabled_tools."""

    disabled_tools: list[str] | None = Field(
        default=None,
        examples=[["delete_file", "write_file"], ["dangerous_tool"]],
        title="Disabled tools",
    )
    """Tools to exclude from this server (blacklist). Mutually exclusive with enabled_tools."""

    always_load: bool = Field(default=False, title="Always load tools")
    """Load this server's tools up front instead of deferring them to tool search.

    Only honored by agents with their own tool search (currently Claude Code),
    which otherwise may hide MCP tools until the model searches for them.
    """

    skills: bool = Field(default=True, title="Use server skills")
    """Discover skills the server publishes through the MCP Skills extension (SEP-2640).

    When enabled, the client opts into the extension, and the server's skills
    become available to the agent owning this connection as ``<server>/<skill>``.
    """

    events: bool = Field(default=False, title="Use server events")
    """Opt into the server's MCP Events extension (SEP-3415, experimental).

    Needed for ``mcp_event`` triggers on this server: they subscribe to its events.
    """

    @model_validator(mode="after")
    def _validate_tool_filters(self) -> Self:
        """Validate that enabled_tools and disabled_tools are mutually exclusive."""
        if self.enabled_tools is not None and self.disabled_tools is not None:
            raise ValueError("Cannot specify both 'enabled_tools' and 'disabled_tools'")
        return self

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is allowed based on enabled/disabled lists.

        Args:
            tool_name: Name of the tool to check

        Returns:
            True if the tool is allowed, False otherwise
        """
        if self.enabled_tools is not None:
            return tool_name in self.enabled_tools
        if self.disabled_tools is not None:
            return tool_name not in self.disabled_tools
        return True

    def needs_tool_filtering(self) -> bool:
        """Check if this config has tool filtering configured."""
        return self.enabled_tools is not None or self.disabled_tools is not None

    def wrap_with_mcp_filter(self) -> StdioMCPServerConfig:
        """Wrap this MCP server with mcp-filter for tool filtering.

        Creates a new StdioMCPServerConfig that runs mcp-filter as a proxy,
        applying the configured enabled_tools/disabled_tools filtering.

        Returns:
            A new StdioMCPServerConfig that wraps the original server with mcp-filter

        Raises:
            NotImplementedError: Subclasses must implement this method
        """
        raise NotImplementedError

    def get_env_vars(self) -> dict[str, str]:
        """Get environment variables for the server process."""
        env = os.environ.copy()
        if self.env:
            env.update(self.env)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def to_pydantic_ai(self) -> MCPToolset[Any]:
        """Convert to a pydantic-ai MCPToolset."""
        raise NotImplementedError

    @property
    def client_id(self) -> str:
        """Generate a unique client ID for this server configuration."""
        raise NotImplementedError

    @classmethod
    def from_string(cls, text: str) -> MCPServerConfig:
        """Create a MCPServerConfig from a string."""
        text = text.strip()
        if text.startswith(("http://", "https://")) and text.endswith("/sse"):
            return SSEMCPServerConfig(url=HttpUrl(text))
        if text.startswith(("http://", "https://")):
            return StreamableHTTPMCPServerConfig(url=HttpUrl(text))
        return StdioMCPServerConfig.from_string(text)


class StdioMCPServerConfig(BaseMCPServerConfig):
    """MCP server started via stdio.

    Uses subprocess communication through standard input/output streams.
    """

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Stdio MCP Server"})

    type: Literal["stdio"] = Field("stdio", init=False)
    """Stdio server coniguration."""

    command: str = Field(
        examples=["python", "node", "pipx", "uvx"],
        title="Command to execute",
    )
    """Command to execute (e.g. "pipx", "python", "node")."""

    args: list[str] = Field(
        default_factory=list,
        examples=[["run", "mcp-server"], ["-m", "my_mcp_server"], ["--debug"]],
        title="Command arguments",
    )
    """Command arguments (e.g. ["run", "some-server", "--debug"])."""

    @classmethod
    def from_string(cls, text: str) -> Self:
        """Create a MCP server from a command string."""
        parts = text.split(maxsplit=1)
        cmd = parts[0]
        args = parts[1].split() if len(parts) > 1 else []
        return cls(command=cmd, args=args)

    @property
    def client_id(self) -> str:
        """Generate a unique client ID for this stdio server configuration."""
        return f"{self.command}_{' '.join(self.args)}"

    def wrap_with_mcp_filter(self) -> StdioMCPServerConfig:
        """Wrap this stdio MCP server with mcp-filter for tool filtering.

        Returns:
            A new StdioMCPServerConfig that wraps this server with mcp-filter
        """
        filter_args = ["mcp-filter", "run", "-t", "stdio", "--stdio-command", self.command]

        # Add original args as a single --stdio-arg
        if self.args:
            filter_args.extend(["--stdio-arg", " ".join(self.args)])

        # Add allowlist (exact tool names)
        if self.enabled_tools:
            filter_args.extend(["-a", ",".join(self.enabled_tools)])

        # Add denylist (regex patterns)
        if self.disabled_tools:
            filter_args.extend(["-d", ",".join(self.disabled_tools)])

        return StdioMCPServerConfig(
            name=self.name,
            command="uvx",
            args=filter_args,
            env=self.env,
            timeout=self.timeout,
        )

    def to_pydantic_ai(self) -> MCPToolset[Any]:
        """Convert to pydantic-ai MCPToolset using a stdio transport."""
        from fastmcp.client.transports import StdioTransport
        from pydantic_ai.mcp import MCPToolset

        transport = StdioTransport(
            command=self.command,
            args=self.args,
            env=self.get_env_vars() if self.env else None,
        )
        return MCPToolset(transport, id=self.name, init_timeout=self.init_timeout)


class SSEMCPServerConfig(BaseMCPServerConfig):
    """MCP server using Server-Sent Events transport.

    Connects to a server over HTTP with SSE for real-time communication.
    """

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "SSE MCP Server"})

    type: Literal["sse"] = Field("sse", init=False)
    """SSE server configuration."""

    url: HttpUrl = Field(
        examples=["https://api.example.com/sse", "http://localhost:8080/events"],
        title="SSE endpoint URL",
    )
    """URL of the SSE server endpoint."""

    headers: dict[str, str] | None = Field(default=None, title="HTTP headers")
    """Headers to send with the SSE request."""

    auth: MCPServerAuthSettings = Field(
        default_factory=MCPServerAuthSettings,
        title="Authentication settings",
    )
    """OAuth settings for the SSE server."""

    @property
    def client_id(self) -> str:
        """Generate a unique client ID for this SSE server configuration."""
        return f"sse_{self.url}"

    def wrap_with_mcp_filter(self) -> StdioMCPServerConfig:
        """Wrap this SSE MCP server with mcp-filter for tool filtering.

        Returns:
            A new StdioMCPServerConfig that wraps this server with mcp-filter
        """
        filter_args = ["mcp-filter", "run", "-t", "http", "--http-url", str(self.url)]

        # Add allowlist (exact tool names)
        if self.enabled_tools:
            filter_args.extend(["-a", ",".join(self.enabled_tools)])

        # Add denylist (regex patterns)
        if self.disabled_tools:
            filter_args.extend(["-d", ",".join(self.disabled_tools)])

        return StdioMCPServerConfig(
            name=self.name,
            command="uvx",
            args=filter_args,
            timeout=self.timeout,
        )

    def to_pydantic_ai(self) -> MCPToolset[Any]:
        """Convert to pydantic-ai MCPToolset using an SSE transport."""
        from fastmcp.client.transports import SSETransport
        from pydantic_ai.mcp import MCPToolset

        transport = SSETransport(url=str(self.url), headers=self.headers)
        return MCPToolset(transport, id=self.name, init_timeout=self.init_timeout)


class StreamableHTTPMCPServerConfig(BaseMCPServerConfig):
    """MCP server using StreamableHttp.

    Connects to a server over HTTP with streamable HTTP.
    """

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Streamable HTTP MCP Server"})

    type: Literal["streamable-http"] = Field("streamable-http", init=False)
    """HTTP server configuration."""

    url: HttpUrl = Field(
        examples=["https://api.example.com/mcp", "http://localhost:8080/stream"],
        title="HTTP endpoint URL",
    )
    """URL of the HTTP server endpoint."""

    headers: dict[str, str] | None = Field(default=None, title="HTTP headers")
    """Headers to send with the HTTP request."""

    auth: MCPServerAuthSettings = Field(
        default_factory=MCPServerAuthSettings,
        title="Authentication settings",
    )
    """OAuth settings for the HTTP server."""

    @property
    def client_id(self) -> str:
        """Generate a unique client ID for this streamable HTTP server configuration."""
        return f"streamable_http_{self.url}"

    def wrap_with_mcp_filter(self) -> StdioMCPServerConfig:
        """Wrap this HTTP MCP server with mcp-filter for tool filtering.

        Returns:
            A new StdioMCPServerConfig that wraps this server with mcp-filter
        """
        filter_args = ["mcp-filter", "run", "-t", "http", "--http-url", str(self.url)]

        # Add allowlist (exact tool names)
        if self.enabled_tools:
            filter_args.extend(["-a", ",".join(self.enabled_tools)])

        # Add denylist (regex patterns)
        if self.disabled_tools:
            filter_args.extend(["-d", ",".join(self.disabled_tools)])

        return StdioMCPServerConfig(
            name=self.name,
            command="uvx",
            args=filter_args,
            timeout=self.timeout,
        )

    def to_pydantic_ai(self) -> MCPToolset[Any]:
        """Convert to pydantic-ai MCPToolset using a streamable HTTP transport."""
        from fastmcp.client.transports import StreamableHttpTransport
        from pydantic_ai.mcp import MCPToolset

        transport = StreamableHttpTransport(url=str(self.url), headers=self.headers)
        return MCPToolset(transport, id=self.name, init_timeout=self.init_timeout)


MCPServerConfig = Annotated[
    StdioMCPServerConfig | SSEMCPServerConfig | StreamableHTTPMCPServerConfig,
    Field(discriminator="type"),
]


def parse_mcp_servers_json(data: dict[str, object]) -> list[MCPServerConfig]:
    """Parse MCP servers from JSON format used by clients (e.g., Zed).

    Expected format:
        {
            "mcpServers": {
                "server_name": {
                    "url": "https://...",
                    "transport": "sse" | "http"  # optional, defaults to http
                },
                "stdio_server": {
                    "command": "python",
                    "args": ["-m", "my_server"]
                },
                ...
            }
        }

    Args:
        data: JSON data containing mcpServers key

    Returns:
        List of parsed MCPServerConfig instances

    Raises:
        ValueError: If data format is invalid or transport type unsupported
    """
    if "mcpServers" not in data:
        raise ValueError("MCP config must contain 'mcpServers' key")

    servers: list[MCPServerConfig] = []
    mcp_servers = data["mcpServers"]
    if not isinstance(mcp_servers, dict):
        raise TypeError("'mcpServers' must be an object")
    for server_name, server_cfg in mcp_servers.items():
        assert isinstance(server_name, str)
        assert isinstance(server_cfg, dict)
        match server_cfg:
            case {"command": str(command), **rest}:
                server: MCPServerConfig = StdioMCPServerConfig(
                    name=server_name,
                    command=command,
                    args=rest.get("args", []),
                    env=rest.get("env"),
                )
            case {"transport": "sse", "url": url}:
                server = SSEMCPServerConfig(name=server_name, url=url)
            case {"transport": "http", "url": url} | {"url": url}:  # Default to HTTP
                server = StreamableHTTPMCPServerConfig(name=server_name, url=url)
            case {"transport": unknown}:
                raise ValueError(f"Unsupported transport type for '{server_name}': {unknown}")
            case _:
                raise ValueError(f"Invalid config for MCP server '{server_name}': {server_cfg}")

        servers.append(server)

    return servers
