"""Command for running agents as an ACP (Agent Client Protocol) server.

This creates an ACP-compatible JSON-RPC 2.0 server that exposes your agents
for bidirectional communication over stdio streams, enabling desktop application
integration with file system access, permission handling, and terminal support.

Configuration is resolved from multiple layers (in precedence order):
1. Global config (~/.config/agentpool/agentpool.yml)
2. Custom config (AGENTPOOL_CONFIG env var)
3. Project config (agentpool.yml in project/git root)
4. Explicit CLI argument (highest precedence)
5. Built-in fallback (only if no agents defined elsewhere)
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Annotated, Literal

import anyenv
from platformdirs import user_log_path
import typer as t

from agentpool_cli import log


if TYPE_CHECKING:
    from aclientp import HttpTransport, Transport


logger = log.get_logger(__name__)


LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _http_transport(
    host: str, port: int, token: str | None, allow_origins: list[str]
) -> HttpTransport:
    """Streamable HTTP transport; refuses to expose the agents without a token.

    Agents run tools (shell, files) on this machine, so an unauthenticated endpoint is
    only allowed on a loopback host.
    """
    from aclientp import HttpTransport
    from aclientp.http_server import bearer_token_auth

    if token is None and host not in LOOPBACK_HOSTS:
        msg = f"Serving on {host!r} requires --token (or AGENTPOOL_ACP_TOKEN)"
        raise t.BadParameter(msg)
    authenticate = bearer_token_auth(token) if token else None
    return HttpTransport(
        host=host, port=port, authenticate=authenticate, allow_origins=allow_origins
    )


def acp_command(  # noqa: PLR0915
    # Too many statements - complex CLI command with many options
    config: Annotated[str | None, t.Argument(help="Path to agent configuration (optional)")] = None,
    show_messages: Annotated[
        bool, t.Option("--show-messages", help="Show message activity in logs")
    ] = False,
    debug_messages: Annotated[
        bool, t.Option("--debug-messages", help="Save raw JSON-RPC messages to debug file")
    ] = False,
    debug_file: Annotated[
        str | None,
        t.Option(
            "--debug-file",
            help="File to save JSON-RPC debug messages (default: acp-debug.jsonl)",
        ),
    ] = None,
    debug_commands: Annotated[
        bool,
        t.Option(
            "--debug-commands",
            help="Enable debug slash commands for testing ACP notifications",
        ),
    ] = False,
    agent: Annotated[
        str | None,
        t.Option(
            "--agent",
            help="Name of specific agent to use (defaults to pool's default agent)",
        ),
    ] = None,
    load_skills: Annotated[
        bool,
        t.Option(
            "--skills/--no-skills",
            help="Load client-side skills from .claude/skills and .agents/skills directories",
        ),
    ] = True,
    transport: Annotated[
        Literal["stdio", "websocket", "http"],
        t.Option(
            "--transport",
            "-t",
            help=(
                "Transport type: stdio (default), websocket, or http (Streamable HTTP and "
                "WebSocket on one endpoint, experimental)"
            ),
        ),
    ] = "stdio",
    host: Annotated[
        str | None,
        t.Option("--host", help="Host to bind (websocket/http; default: localhost)"),
    ] = None,
    port: Annotated[
        int | None,
        t.Option("--port", help="Port to listen on (websocket/http; default: 8765)"),
    ] = None,
    token: Annotated[
        str | None,
        t.Option(
            "--token",
            envvar="AGENTPOOL_ACP_TOKEN",
            help=(
                "Bearer token clients must send (http). Required when binding to a "
                "non-loopback host"
            ),
        ),
    ] = None,
    allow_origin: Annotated[
        list[str] | None,
        t.Option("--allow-origin", help="CORS origin for browser clients (http); repeatable"),
    ] = None,
    ws_host: Annotated[
        str,
        t.Option(
            "--ws-host",
            help="Deprecated alias of --host",
        ),
    ] = "localhost",
    ws_port: Annotated[
        int,
        t.Option(
            "--ws-port",
            help="Deprecated alias of --port",
        ),
    ] = 8765,
    mcp_config: Annotated[
        str | None,
        t.Option(
            "--mcp-config",
            help='MCP servers configuration as JSON (format: {"mcpServers": {...}})',
        ),
    ] = None,
) -> None:
    r"""Run agents as an ACP (Agent Client Protocol) server.

    This creates an ACP-compatible JSON-RPC 2.0 server that communicates over stdio
    streams, enabling your agents to work with desktop applications that support
    the Agent Client Protocol.

    Configuration Layers (merged in order, later overrides earlier):

    1. Global config - ~/.config/agentpool/agentpool.yml for user preferences
    2. Custom config - AGENTPOOL_CONFIG env var for CI/deployment overrides
    3. Project config - agentpool.yml in project/git root for project-specific settings
    4. Explicit config - CLI argument (highest precedence)
    5. Built-in fallback - Only used if no agents defined in any layer

    Agent Selection:
    Use --agent to specify which agent to use by name. Without this option,
    the pool's default agent is used (set via 'default_agent' in config,
    or falls back to the first agent).
    """
    from aclientp import StdioTransport, WebSocketTransport

    from agentpool import log
    from agentpool.config_resources import ACP_ASSISTANT
    from agentpool.models.manifest import AgentsManifest
    from agentpool_config.resolution import resolve_config
    from agentpool_server.acp_server import ACPServer

    # Build transport config
    bind_host = host or ws_host
    bind_port = port or ws_port
    match transport:
        case "websocket":
            transport_config: Transport = WebSocketTransport(host=bind_host, port=bind_port)
        case "http":
            transport_config = _http_transport(bind_host, bind_port, token, allow_origin or [])
        case "stdio":
            transport_config = StdioTransport()

    # Always log to file with rollover
    log_dir = user_log_path("agentpool", appauthor=False)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "acp.log"
    log.configure_logging(force=True, log_file=str(log_file))
    logger.info("Configured file logging with rollover", log_file=str(log_file))

    # Resolve configuration from all layers
    # fallback_config is only used if no agents are defined in any layer
    try:
        resolved = resolve_config(explicit_path=config, fallback_config=ACP_ASSISTANT)
    except ValueError as e:
        raise t.BadParameter(str(e)) from e

    # Log which config layers were used
    if resolved.layers:
        sources = [f"{layer.source}:{layer.path}" for layer in resolved.layers if layer.path]
        logger.info("Config layers loaded", sources=sources, transport=transport)
    else:
        logger.info("Starting ACP server with built-in defaults only", transport=transport)

    # Load manifest from merged config data
    try:
        manifest = AgentsManifest.model_validate(resolved.data)
        if resolved.primary_path:
            manifest = manifest.model_copy(update={"config_file_path": resolved.primary_path})
    except Exception as e:
        raise t.BadParameter(f"Invalid merged configuration: {e}") from e

    acp_server = ACPServer.from_config(
        manifest,
        debug_messages=debug_messages,
        debug_file=debug_file or "acp-debug.jsonl" if debug_messages else None,
        debug_commands=debug_commands,
        agent=agent,
        load_skills=load_skills,
        transport=transport_config,
    )

    # Inject MCP servers from --mcp-config if provided
    # TODO: Consider adding to specific agent's MCP manager instead of pool-level
    # for better isolation (currently all agents in pool share these servers)
    if mcp_config:
        from agentpool_config.mcp_server import parse_mcp_servers_json

        try:
            mcp_data = anyenv.load_json(mcp_config)
        except anyenv.JsonLoadError as e:
            raise t.BadParameter(f"Invalid JSON in --mcp-config: {e}") from e

        try:
            servers = parse_mcp_servers_json(mcp_data)
        except ValueError as e:
            raise t.BadParameter(str(e)) from e

        for server in servers:
            acp_server.pool.mcp.add_server_config(server)
            logger.info("Added MCP server from --mcp-config", server_name=server.name)

    if show_messages:
        logger.info("Message activity logging enabled")
    if debug_messages:
        debug_path = debug_file or "acp-debug.jsonl"
        logger.info("Raw JSON-RPC message debugging enabled", path=debug_path)
    if debug_commands:
        logger.info("Debug slash commands enabled")
    logger.info("Server PID", pid=os.getpid())

    async def run_acp_server() -> None:
        try:
            async with acp_server:
                await acp_server.start()
        except KeyboardInterrupt:
            logger.info("ACP server shutdown requested")
        except Exception as e:
            logger.exception("ACP server error")
            raise t.Exit(1) from e

    asyncio.run(run_acp_server())


if __name__ == "__main__":
    t.run(acp_command)
