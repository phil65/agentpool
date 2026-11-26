"""History management commands."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

import typer as t

from agentpool_cli import log, resolve_agent_config
from agentpool_cli.cli_types import GroupBy
from agentpool_cli.common import OutputFormat, format_output, output_format_opt


if TYPE_CHECKING:
    from agentpool_storage.base import StorageProvider


logger = log.get_logger(__name__)

help_text = "Conversation history management"
history_cli = t.Typer(name="history", help=help_text, no_args_is_help=True)

AGENT_NAME_HELP = "Agent name (shows all if not provided)"
SINCE_HELP = "Show conversations since (YYYY-MM-DD or YYYY-MM-DD HH:MM)"
PERIOD_HELP = "Show conversations from last period (1h, 2d, 1w, 1m)"
COMPACT_HELP = "Show only first/last message of conversations"
TOKEN_HELP = "Include token usage statistics"
CONFIG_HELP = "Override agent config path"


def get_history_provider(config_path: str) -> StorageProvider:
    """Get history provider from manifest config.

    Args:
        config_path: Path to agent configuration file

    Returns:
        Storage provider configured for history operations
    """
    from agentpool import AgentsManifest
    from agentpool.storage import StorageManager

    manifest = AgentsManifest.from_file(config_path)
    storage = StorageManager(manifest.storage)
    return storage.get_history_provider()


@history_cli.command(name="show")
def show_history(
    agent_name: Annotated[str | None, t.Argument(help=AGENT_NAME_HELP)] = None,
    config: Annotated[str | None, t.Option("--config", "-c", help=CONFIG_HELP)] = None,
    # Time-based filtering
    since: Annotated[datetime | None, t.Option("--since", "-s", help=SINCE_HELP)] = None,
    period: Annotated[str | None, t.Option("--period", "-p", help=PERIOD_HELP)] = None,
    # Content filtering
    query: Annotated[
        str | None, t.Option("--query", "-q", help="Search in message content")
    ] = None,
    model: Annotated[str | None, t.Option("--model", "-m", help="Filter by model used")] = None,
    # Output control
    limit: Annotated[int, t.Option("--limit", "-n", help="Number of conversations")] = 10,
    compact: Annotated[bool, t.Option("--compact", help=COMPACT_HELP)] = False,
    tokens: Annotated[bool, t.Option("--tokens", "-t", help=TOKEN_HELP)] = False,
    output_format: OutputFormat = output_format_opt,
) -> None:
    """Show conversation history with filtering options.

    Examples:
        # Show last 5 conversations
        agentpool history show -n 5

        # Show conversations from last 24 hours
        agentpool history show --period 24h

        # Show conversations since specific date
        agentpool history show --since 2024-01-01

        # Search for specific content
        agentpool history show --query "database schema"

        # Show GPT-5 conversations with token usage
        agentpool history show --model gpt-5 --tokens

        # Compact view of recent conversations
        agentpool history show --period 1d --compact
    """
    import anyio

    try:
        # Resolve config and get provider
        config_path = resolve_agent_config(config)
        provider = get_history_provider(config_path)

        async def main() -> None:
            async with provider:
                results = await provider.get_filtered_conversations(
                    agent_name=agent_name,
                    period=period,
                    since=since,
                    query=query,
                    model=model,
                    limit=limit,
                    compact=compact,
                    include_tokens=tokens,
                )
                format_output(results, output_format)

        anyio.run(main)

    except Exception as e:
        logger.exception("Failed to show history")
        raise t.Exit(1) from e


@history_cli.command(name="stats")
def show_stats(
    agent_name: Annotated[str | None, t.Argument(help=AGENT_NAME_HELP)] = None,
    config: Annotated[str | None, t.Option("--config", "-c", help=CONFIG_HELP)] = None,
    period: Annotated[
        str, t.Option("--period", "-p", help="Time period (1h, 1d, 1w, 1m, 1y)")
    ] = "1d",
    group_by: Annotated[GroupBy, t.Option("--group-by", "-g", help="Group by")] = "agent",
    output_format: OutputFormat = output_format_opt,
) -> None:
    """Show usage statistics.

    Examples:
        # Show stats for all agents
        agentpool history stats

        # Show daily stats for specific agent
        agentpool history stats myagent --group-by day

        # Show model usage for last week
        agentpool history stats --period 1w --group-by model
    """
    import anyio

    from agentpool.utils.time_utils import parse_time_period
    from agentpool_storage.formatters import format_stats
    from agentpool_storage.models import StatsFilters

    try:
        # Resolve config and get provider
        config_path = resolve_agent_config(config)
        provider = get_history_provider(config_path)

        # Create filters
        cutoff = datetime.now(UTC) - parse_time_period(period)
        filters = StatsFilters(cutoff=cutoff, group_by=group_by, agent_name=agent_name)

        async def main() -> None:
            async with provider:
                stats = await provider.get_session_stats(filters)
            formatted = format_stats(stats, period, group_by)
            format_output(formatted, output_format)

        anyio.run(main)

    except Exception as e:
        logger.exception("Failed to show stats")
        raise t.Exit(1) from e


@history_cli.command(name="reset")
def reset_history(
    config: Annotated[str | None, t.Option("--config", "-c", help=CONFIG_HELP)] = None,
    confirm: Annotated[bool, t.Option("--confirm", "-y", help="Confirm deletion")] = False,
    agent_name: Annotated[
        str | None, t.Option("--agent", "-a", help="Only delete for specific agent")
    ] = None,
    hard: Annotated[
        bool, t.Option("--hard", help="Drop and recreate tables (for schema changes)")
    ] = False,
) -> None:
    """Reset (clear) conversation history.

    Examples:
        # Clear all history (with confirmation)
        agentpool history reset

        # Clear without confirmation
        agentpool history reset --confirm

        # Clear history for specific agent
        agentpool history reset --agent myagent

        # Drop and recreate tables (for schema changes)
        agentpool history reset --hard --confirm
    """
    import anyio

    try:
        # Resolve config and get provider
        config_path = resolve_agent_config(config)
        provider = get_history_provider(config_path)

        if not confirm:
            what = f" for {agent_name}" if agent_name else ""
            msg = f"This will delete all history{what}. Are you sure? [y/N] "
            if input(msg).lower() != "y":
                print("Operation cancelled.")
                return

        async def main() -> None:
            async with provider:
                conv_count, msg_count = await provider.reset(agent_name=agent_name, hard=hard)

                what = f" for {agent_name}" if agent_name else ""
                print(f"Deleted {conv_count} conversations and {msg_count} messages{what}.")

        anyio.run(main)

    except Exception as e:
        logger.exception("Failed to reset history")
        raise t.Exit(1) from e
