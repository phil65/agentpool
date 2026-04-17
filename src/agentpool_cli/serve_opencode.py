"""Command for running agents as an OpenCode-compatible server.

This creates an HTTP server that implements the OpenCode API protocol,
allowing OpenCode TUI and SDK clients to interact with AgentPool agents.

Configuration is resolved from multiple layers (in precedence order):
1. Global config (~/.config/agentpool/agentpool.yml)
2. Custom config (AGENTPOOL_CONFIG env var)
3. Project config (agentpool.yml in project/git root)
4. Explicit CLI argument (highest precedence)
5. Built-in fallback (only if no agents defined elsewhere)
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from platformdirs import user_log_path
import typer as t

from agentpool_cli import log


logger = log.get_logger(__name__)


def opencode_command(
    config: Annotated[str | None, t.Argument(help="Path to agent configuration (optional)")] = None,
    host: Annotated[
        str,
        t.Option("--host", "-h", help="Host to bind to"),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        t.Option("--port", "-p", help="Port to listen on"),
    ] = 4096,
    agent: Annotated[
        str | None,
        t.Option(
            "--agent",
            help="Name of specific agent to use (defaults to pool's default agent)",
        ),
    ] = None,
    working_dir: Annotated[
        str | None,
        t.Option(
            "--working-dir",
            "-w",
            help="Working directory for file operations (defaults to current directory)",
        ),
    ] = None,
) -> None:
    """Run agents as an OpenCode-compatible HTTP server.

    This creates an HTTP server implementing the OpenCode API protocol,
    enabling your AgentPool agents to work with OpenCode TUI and SDK clients.

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
    from agentpool import AgentPool, log as ap_log
    from agentpool.config_resources import CLAUDE_CODE_ASSISTANT
    from agentpool.models.manifest import AgentsManifest
    from agentpool.observability import registry
    from agentpool_config.resolution import resolve_config
    from agentpool_server.opencode_server.server import OpenCodeServer

    # Resolve configuration from all layers FIRST (before logging)
    # fallback_config is only used if no agents are defined in any layer
    try:
        resolved = resolve_config(
            explicit_path=config,
            fallback_config=CLAUDE_CODE_ASSISTANT,
        )
    except ValueError as e:
        raise t.BadParameter(str(e)) from e

    # Load manifest from merged config data
    try:
        manifest = AgentsManifest.model_validate(resolved.data)
        if resolved.primary_path:
            manifest = manifest.model_copy(update={"config_file_path": resolved.primary_path})
    except Exception as e:
        raise t.BadParameter(f"Invalid merged configuration: {e}") from e

    # Initialize observability BEFORE configuring logging
    # This ensures logfire is configured before StructlogProcessor is added
    registry.configure_observability(manifest.observability)
    # Always log to file with rollover
    log_dir = user_log_path("agentpool", appauthor=False)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "opencode.log"
    ap_log.configure_logging(force=True, log_file=str(log_file))
    logger.info("Configured file logging with rollover", log_file=str(log_file))
    # Log which config layers were used
    if resolved.layers:
        sources = [f"{layer.source}:{layer.path}" for layer in resolved.layers if layer.path]
        logger.info("Config layers loaded", sources=sources, host=host, port=port)
    else:
        logger.info("Starting OpenCode server with built-in defaults only", host=host, port=port)
    # Load agent from merged manifest
    pool = AgentPool(manifest, main_agent_name=agent)

    async def run_server() -> None:
        async with pool:
            # Load agent rules from global and project locations
            await pool.main_agent.load_rules(working_dir)

            server = OpenCodeServer(
                pool.main_agent,
                host=host,
                port=port,
                working_dir=working_dir,
            )
            logger.info("Server starting", url=f"http://{host}:{port}")
            await server.run_async()

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("OpenCode server shutdown requested")
    except Exception as e:
        logger.exception("OpenCode server error")
        raise t.Exit(1) from e


if __name__ == "__main__":
    t.run(opencode_command)
