"""Command for running agents as an MCP server."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from typing import TYPE_CHECKING, Annotated, Any, Literal

import typer as t

from agentpool_cli import log


# Duplicated from agentpool_config.pool_server to avoid heavy imports at CLI startup
TransportType = Literal["stdio", "sse", "streamable-http"]

if TYPE_CHECKING:
    from agentpool import ChatMessage


logger = log.get_logger(__name__)


def serve_command(
    config: Annotated[str, t.Argument(help="Path to agent configuration")],
    transport: Annotated[TransportType, t.Option(help="Transport type")] = "stdio",
    host: Annotated[
        str, t.Option(help="Host to bind server to (sse/streamable-http only)")
    ] = "localhost",
    port: Annotated[int, t.Option(help="Port to listen on (sse/streamable-http only)")] = 3001,
    show_messages: Annotated[
        bool, t.Option("--show-messages", help="Show message activity")
    ] = False,
    background_tasks: Annotated[
        bool,
        t.Option(
            "--background-tasks",
            help="Let clients run agent tools as MCP background tasks (needs mcp-tasks extra)",
        ),
    ] = False,
    tasks_url: Annotated[
        str | None,
        t.Option(help="Task backend: memory:// (default) or a Redis URL to survive restarts"),
    ] = None,
    tasks_ttl: Annotated[
        int | None,
        t.Option(help="Seconds to keep a task and its result, counted from its start"),
    ] = None,
) -> None:
    """Run agents as an MCP server.

    This makes agents available as tools to other applications, regardless of
    whether pool_server is configured in the manifest.
    """

    def on_message(message: ChatMessage[Any]) -> None:
        print(message.format(style="simple"))

    async def run_server() -> None:

        from agentpool import AgentPool, AgentsManifest
        from agentpool_config.pool_server import MCPPoolServerConfig
        from agentpool_server.mcp_server.server import MCPServer

        logger.info("Server PID", pid=os.getpid())
        # Load manifest and create pool (without server config)
        manifest = AgentsManifest.from_file(config)
        pool = AgentPool(manifest)
        # Create server config and server externally
        server_config = MCPPoolServerConfig(
            enabled=True,
            transport=transport,
            host=host,
            port=port,
            background_tasks=background_tasks,
            tasks_url=tasks_url,
            tasks_ttl=timedelta(seconds=tasks_ttl) if tasks_ttl is not None else None,
        )
        server = MCPServer(pool, server_config)
        async with pool, server:
            if show_messages:
                for agent in pool.all_agents.values():
                    agent.message_sent.connect(on_message)

            try:
                await server.start()  # Blocks until server stops
            except KeyboardInterrupt:
                logger.info("Server shutdown requested")

    asyncio.run(run_server())
