"""Command for serving an agent pool through the OpenCode V2 HTTP API.

Point an OpenCode V2 client at the server with ``opencode --server http://HOST:PORT``.

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
    host: Annotated[str, t.Option("--host", "-h", help="Host to bind to")] = "127.0.0.1",
    port: Annotated[int, t.Option("--port", "-p", help="Port to listen on")] = 4096,
    agent: Annotated[
        str | None,
        t.Option("--agent", help="Default agent for new sessions (defaults to the pool's)"),
    ] = None,
    working_dir: Annotated[
        str | None,
        t.Option(
            "--working-dir",
            "-w",
            help="Default location directory (defaults to the current directory)",
        ),
    ] = None,
    password: Annotated[
        str | None,
        t.Option(
            "--password",
            help="Basic auth password (username 'opencode'). Defaults to OPENCODE_PASSWORD.",
        ),
    ] = None,
) -> None:
    """Serve the agent pool through the OpenCode V2 HTTP API.

    Every pool agent and team is exposed as an OpenCode agent. Sessions are bound to
    one agent at a time and can switch agent and model while they run. Connect with
    ``opencode --server http://127.0.0.1:4096``.
    """
    from agentpool import AgentPool, log as ap_log
    from agentpool.config_resources import CLAUDE_CODE_ASSISTANT
    from agentpool.models.manifest import AgentsManifest
    from agentpool.observability import registry
    from agentpool_config.resolution import resolve_config
    from agentpool_server.opencode_v2_server.server import OpenCodeV2Server

    try:
        resolved = resolve_config(explicit_path=config, fallback_config=CLAUDE_CODE_ASSISTANT)
    except ValueError as e:
        raise t.BadParameter(str(e)) from e
    try:
        manifest = AgentsManifest.model_validate(resolved.data)
        if resolved.primary_path:
            manifest = manifest.model_copy(update={"config_file_path": resolved.primary_path})
    except Exception as e:
        raise t.BadParameter(f"Invalid merged configuration: {e}") from e

    registry.configure_observability(manifest.observability)
    log_dir = user_log_path("agentpool", appauthor=False)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "opencode.log"
    ap_log.configure_logging(force=True, log_file=str(log_file))
    if resolved.layers:
        sources = [f"{layer.source}:{layer.path}" for layer in resolved.layers if layer.path]
        logger.info("Config layers loaded", sources=sources, host=host, port=port)
    pool = AgentPool(manifest, main_agent_name=agent)

    async def run_server() -> None:
        async with pool:
            for pool_agent in pool.all_agents.values():
                await pool_agent.load_rules(working_dir)
            server = OpenCodeV2Server(
                pool,
                host=host,
                port=port,
                working_dir=working_dir,
                password=password,
                raise_exceptions=True,
            )
            url = f"http://{host}:{port}"
            logger.info("OpenCode server starting", url=url)
            t.echo(f"OpenCode server listening on {url} (connect: opencode --server {url})")
            await server.start()

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("OpenCode server shutdown requested")
    except Exception as e:
        logger.exception("OpenCode server error")
        raise t.Exit(1) from e


if __name__ == "__main__":
    t.run(opencode_command)
