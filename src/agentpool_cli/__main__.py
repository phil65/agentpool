"""CLI interface for AgentPool."""

from __future__ import annotations

from importlib import metadata

import typer as t

from agentpool_cli import log
from agentpool_cli.agent import add_agent_file, list_agents, list_configs, set_active_file
from agentpool_cli.cli_types import LogLevel  # noqa: TC001
from agentpool_cli.config_info import config_cli
from agentpool_cli.history import history_cli
from agentpool_cli.run import run_command
from agentpool_cli.serve_acp import acp_command
from agentpool_cli.serve_agui import agui_command
from agentpool_cli.serve_api import api_command
from agentpool_cli.serve_mcp import serve_command
from agentpool_cli.serve_opencode import opencode_command
from agentpool_cli.serve_vercel import vercel_command
from agentpool_cli.store import ConfigStore
from agentpool_cli.task import task_command
from agentpool_cli.ui import ui_app
from agentpool_cli.watch import watch_command


agent_store = ConfigStore("agents.json")

MAIN_HELP = "🤖 AgentPool CLI - Run and manage LLM agents"


def get_command_help(base_help: str) -> str:
    """Get command help text with active config information."""
    if active := agent_store.get_active():
        return f"{base_help}\n\n(Using config: {active})"
    return f"{base_help}\n\n(No active config set)"


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        version = metadata.version("agentpool")
        t.echo(f"agentpool version {version}")
        raise t.Exit


def main(
    ctx: t.Context,
    version: bool = t.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit",
        callback=version_callback,
        is_eager=True,
    ),
    log_level: LogLevel = t.Option("info", "--log-level", "-l", help="Log level"),  # noqa: B008
) -> None:
    """🤖 AgentPool CLI - Run and manage LLM agents."""
    # Configure logging globally
    log.configure_logging(level=log_level.upper())

    # Store log level in context for commands that might need it
    ctx.ensure_object(dict)
    ctx.obj["log_level"] = log_level


# Create CLI app
cli = t.Typer(name="AgentPool", help=MAIN_HELP, no_args_is_help=True)
cli.callback()(main)

cli.command(name="add")(add_agent_file)
cli.command(name="run")(run_command)
cli.command(name="list-agents")(list_agents)
cli.command(name="list-configs")(list_configs)
cli.command(name="set")(set_active_file)
cli.command(name="watch")(watch_command)
cli.command(name="serve-acp")(acp_command)
cli.command(name="serve-agui")(agui_command)
cli.command(name="serve-mcp")(serve_command)
cli.command(name="serve-api")(api_command)
cli.command(name="serve-opencode")(opencode_command)
cli.command(name="serve-vercel")(vercel_command)
cli.command(name="task")(task_command)

cli.add_typer(config_cli, name="config")
cli.add_typer(history_cli, name="history")
cli.add_typer(ui_app, name="ui")


if __name__ == "__main__":
    cli()
