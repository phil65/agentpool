"""MCP server management commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import HttpUrl
from slashed import CommandContext, CommandError

from agentpool_commands.base import NodeCommand


if TYPE_CHECKING:
    from agentpool.agents.context import AgentContext
    from agentpool.messaging import MessageNode
    from agentpool_config.mcp_server import MCPServerConfig


class AddMCPServerCommand(NodeCommand):
    """Add a local MCP server via stdio transport.

    Registers a new MCP server that runs as a subprocess.

    Options:
      --args "arg1|arg2"       Pipe-separated command arguments
      --env "KEY=val|KEY2=val" Pipe-separated environment variables

    Examples:
      /add-mcp-server myserver npx --args "mcp-server-filesystem|/tmp"
      /add-mcp-server graphql npx --args "mcp-graphql" --env "ENDPOINT=https://api.example.com/graphql"
    """

    name = "add-mcp-server"
    category = "integrations"

    @classmethod
    def supports_node(cls, node: MessageNode[Any, Any]) -> bool:
        from agentpool import Agent

        return isinstance(node, Agent)

    async def execute_command(
        self,
        ctx: CommandContext[AgentContext],
        name: str,
        command: str,
        *,
        args: str | None = None,
        env: str | None = None,
    ) -> None:
        """Add a local MCP server.

        Args:
            ctx: Command context
            name: Unique name for the MCP server
            command: Command to execute for the server
            args: Pipe-separated command arguments
            env: Pipe-separated environment variables (KEY=value format)
        """
        from agentpool_config.mcp_server import StdioMCPServerConfig

        try:
            # Parse arguments
            arg_list = [a.strip() for a in args.split("|")] if args else []
            # Parse environment variables
            env_dict: dict[str, str] = {}
            if env:
                for pair in env.split("|"):
                    if "=" in pair:
                        key, value = pair.split("=", 1)
                        env_dict[key.strip()] = value.strip()

            config = StdioMCPServerConfig(name=name, command=command, args=arg_list, env=env_dict)
            await ctx.context.agent.mcp.setup_server(config, add_to_config=True)
            await ctx.print(f"**Added MCP server** `{name}` with command: `{command}`")

        except Exception as e:
            raise CommandError(f"Failed to add MCP server: {e}") from e


class AddRemoteMCPServerCommand(NodeCommand):
    """Add a remote MCP server via HTTP transport.

    Connects to an MCP server running on a remote URL.

    Options:
      --transport sse|streamable-http   Transport type (default: streamable-http)

    Examples:
      /add-remote-mcp-server myapi https://api.example.com/mcp
      /add-remote-mcp-server legacy https://old.api.com/mcp --transport sse
    """

    name = "add-remote-mcp-server"
    category = "integrations"

    @classmethod
    def supports_node(cls, node: MessageNode[Any, Any]) -> bool:
        from agentpool import Agent

        return isinstance(node, Agent)

    async def execute_command(
        self,
        ctx: CommandContext[AgentContext],
        name: str,
        url: str,
        *,
        transport: Literal["sse", "streamable-http"] = "streamable-http",
    ) -> None:
        """Add a remote MCP server.

        Args:
            ctx: Command context
            name: Unique name for the MCP server
            url: Server URL endpoint
            transport: HTTP transport type (sse or streamable-http)
        """
        from agentpool_config.mcp_server import SSEMCPServerConfig, StreamableHTTPMCPServerConfig

        try:
            config: MCPServerConfig
            if transport == "sse":
                config = SSEMCPServerConfig(name=name, url=HttpUrl(url))
            else:
                config = StreamableHTTPMCPServerConfig(name=name, url=HttpUrl(url))

            await ctx.context.agent.mcp.setup_server(config, add_to_config=True)
            await ctx.print(f"**Added remote MCP server** `{name}` at `{url}` using {transport}")

        except Exception as e:
            raise CommandError(f"Failed to add remote MCP server: {e}") from e


class ListMCPServersCommand(NodeCommand):
    """List all configured MCP servers.

    Shows server name, type, and connection status.
    """

    name = "list-mcp-servers"
    category = "integrations"

    @classmethod
    def supports_node(cls, node: MessageNode[Any, Any]) -> bool:
        from agentpool import Agent

        return isinstance(node, Agent)

    async def execute_command(self, ctx: CommandContext[AgentContext]) -> None:
        """List all MCP servers.

        Args:
            ctx: Command context
        """
        from agentpool_commands.markdown_utils import format_table

        servers = ctx.context.agent.mcp.providers
        if not servers:
            await ctx.print("No MCP servers configured.")
            return

        rows = []
        for server in servers:
            server_type = type(server.server).__name__.replace("MCPServerConfig", "")
            rows.append({
                "Name": server.name,
                "Type": server_type,
                "Status": "connected" if server.client.connected else "disconnected",
            })

        headers = ["Name", "Type", "Status"]
        table = format_table(headers, rows)
        await ctx.print(f"## MCP Servers\n\n{table}")
