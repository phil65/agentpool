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
    from agentpool_config.pool_server import MCPPoolServerConfig


logger = log.get_logger(__name__)


def resolve_server_config(
    base: MCPPoolServerConfig,
    *,
    transport: TransportType | None = None,
    host: str | None = None,
    port: int | None = None,
    background_tasks: bool | None = None,
    tasks_url: str | None = None,
    tasks_ttl: int | None = None,
) -> MCPPoolServerConfig:
    """The manifest's ``pool_server`` settings, with the options given on the command line."""
    given: dict[str, Any] = {
        "transport": transport,
        "host": host,
        "port": port,
        "background_tasks": background_tasks,
        "tasks_url": tasks_url,
        "tasks_ttl": timedelta(seconds=tasks_ttl) if tasks_ttl is not None else None,
    }
    updates = {key: value for key, value in given.items() if value is not None}
    return base.model_copy(update={"enabled": True, **updates})


def serve_command(
    config: Annotated[str, t.Argument(help="Path to agent configuration")],
    transport: Annotated[
        TransportType | None, t.Option(help="Transport type (default: stdio)")
    ] = None,
    host: Annotated[
        str | None,
        t.Option(help="Host to bind server to, sse/streamable-http only (default: localhost)"),
    ] = None,
    port: Annotated[
        int | None,
        t.Option(help="Port to listen on, sse/streamable-http only (default: 3001)"),
    ] = None,
    show_messages: Annotated[
        bool, t.Option("--show-messages", help="Show message activity")
    ] = False,
    background_tasks: Annotated[
        bool | None,
        t.Option(
            "--background-tasks/--no-background-tasks",
            help="Let clients run agent tools as MCP background tasks (needs mcp-tasks extra)",
        ),
    ] = None,
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

    Settings come from the manifest's ``pool_server`` section (type ``mcp``), and the
    options given here override them. Without the section, the defaults apply.
    """

    def on_message(message: ChatMessage[Any]) -> None:
        print(message.format(style="simple"))

    async def run_server() -> None:

        from agentpool import AgentPool, AgentsManifest
        from agentpool_server.mcp_server.server import MCPServer

        logger.info("Server PID", pid=os.getpid())
        manifest = AgentsManifest.from_file(config)
        pool = AgentPool(manifest)
        server_config = resolve_server_config(
            manifest.pool_server,
            transport=transport,
            host=host,
            port=port,
            background_tasks=background_tasks,
            tasks_url=tasks_url,
            tasks_ttl=tasks_ttl,
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
