"""Command for running agents as a completions API server."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Annotated, Any

import typer as t

from agentpool_cli import resolve_agent_config
from agentpool_cli.log import get_logger


if TYPE_CHECKING:
    from agentpool import ChatMessage


logger = get_logger(__name__)


def api_command(
    ctx: t.Context,
    config: Annotated[str | None, t.Argument(help="Path to agent configuration")] = None,
    host: Annotated[str, t.Option(help="Host to bind server to")] = "localhost",
    port: Annotated[int, t.Option(help="Port to listen on")] = 8000,
    cors_origin: Annotated[
        list[str] | None,
        t.Option(
            "--cors-origin",
            help="Browser origin allowed to call the API (repeatable; '*' needs --api-key)",
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        t.Option(
            "--api-key",
            envvar="AGENTPOOL_API_KEY",
            help="Require 'Authorization: Bearer <key>' (needed for a non-loopback --host)",
        ),
    ] = None,
    agent: Annotated[
        str | None,
        t.Option("--agent", help="Name of specific agent to use (defaults to pool's default)"),
    ] = None,
    show_messages: Annotated[
        bool, t.Option("--show-messages", help="Show message activity")
    ] = False,
) -> None:
    """Run an agent as an OpenAI-compatible completions API server.

    This creates an OpenAI-compatible API server backed by a single agent.
    Model listing uses the agent's model discovery.

    Without --api-key the server only binds to loopback hosts and needs no
    authentication; CORS is off unless --cors-origin is given.
    """
    import uvicorn

    from agentpool import AgentPool, AgentsManifest
    from agentpool_server.openai_api_server.server import OpenAIAPIServer

    logger.info("Server PID", pid=os.getpid())

    def on_message(message: ChatMessage[Any]) -> None:
        print(message.format(style="simple"))

    try:
        config_path = resolve_agent_config(config)
    except ValueError as e:
        msg = str(e)
        raise t.BadParameter(msg) from e
    manifest = AgentsManifest.from_file(config_path)
    pool = AgentPool(manifest, main_agent_name=agent)

    if show_messages:
        pool.main_agent.message_sent.connect(on_message)

    try:
        server = OpenAIAPIServer(
            pool, host=host, port=port, cors_origins=cors_origin or (), api_key=api_key
        )
    except ValueError as e:
        raise t.BadParameter(str(e)) from e

    # Get log level from the global context
    log_level = ctx.obj.get("log_level", "info") if ctx.obj else "info"
    uvicorn.run(server.app, host=server.host, port=server.port, log_level=log_level.lower())


if __name__ == "__main__":
    import typer

    typer.run(api_command)
