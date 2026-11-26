"""MCP server management commands."""

from typing import TYPE_CHECKING, Any, Literal

from pydantic import HttpUrl
from slashed import CommandContext, CommandError

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool_commands.base import NodeCommand


if TYPE_CHECKING:
    from agentpool.agents.base_agent import BaseAgent
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


class MCPCommand(NodeCommand):
    """Show the agent's MCP servers, or reconnect, enable or disable one.

    The servers are the agent's: for Claude Code, Codex and OpenCode the ones their
    backend connects, otherwise the ones agentpool connects for the agent.

    Examples:
      /mcp                     Status of every MCP server
      /mcp reconnect github    Drop the connection and connect again
      /mcp disable github      Disconnect it: its tools go away until enabled
      /mcp enable github       Connect it again
    """

    name = "mcp"
    category = "integrations"

    @classmethod
    def supports_node(cls, node: MessageNode[Any, Any]) -> bool:
        from agentpool.agents.base_agent import BaseAgent

        return isinstance(node, BaseAgent)

    async def execute_command(
        self,
        ctx: CommandContext[AgentContext],
        action: str | None = None,
        server: str | None = None,
    ) -> None:
        """Show MCP server status, or act on one server.

        Args:
            ctx: Command context
            action: reconnect, enable or disable (none: show the status)
            server: The MCP server to act on, as the status lists it
        """
        from agentpool.agents.exceptions import UnsupportedOperationError

        agent = ctx.context.agent
        if action is None:
            await ctx.print(await _mcp_status_table(agent))
            return
        if action not in _ACTIONS:
            msg = f"Unknown action {action!r}: use {', '.join(_ACTIONS)}, or none for the status"
            raise CommandError(msg)
        if server is None:
            raise CommandError(f"Which server? /mcp {action} <server>")
        try:
            match action:
                case "reconnect":
                    await agent.reconnect_mcp_server(server)
                case "enable" | "disable":
                    await agent.set_mcp_server_enabled(server, action == "enable")
        except KeyError as e:
            raise CommandError(str(e.args[0])) from e
        except UnsupportedOperationError as e:
            raise CommandError(str(e)) from e
        except Exception as e:
            msg = f"Could not {action} MCP server {server!r}: {e}"
            raise CommandError(msg) from e
        status = (await agent.get_mcp_server_info()).get(server)
        state = f" (now {status.status})" if status is not None else ""
        await ctx.print(f"MCP server **{server}**: {_ACTIONS[action]}{state}")


_ACTIONS = {"reconnect": "reconnected", "enable": "enabled", "disable": "disabled"}


async def _mcp_status_table(agent: BaseAgent[Any, Any]) -> str:
    from agentpool_commands.markdown_utils import format_table

    servers = await agent.get_mcp_server_info()
    if not servers:
        return "No MCP servers configured."
    rows = [
        {
            "Name": name,
            "Status": info.status,
            "Type": info.server_type,
            "Error": info.error or "",
        }
        for name, info in sorted(servers.items())
    ]
    return f"## MCP Servers\n\n{format_table(['Name', 'Status', 'Type', 'Error'], rows)}"
