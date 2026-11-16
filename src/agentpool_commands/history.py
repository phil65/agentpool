"""History management commands."""

from __future__ import annotations

from slashed import CommandContext, CommandError

from agentpool.messaging.context import NodeContext  # noqa: TC001
from agentpool_commands.base import NodeCommand


class SearchHistoryCommand(NodeCommand):
    """Search conversation history.

    Search through past conversations with optional filtering.

    Options:
      --hours N      Look back N hours (default: 24)
      --limit N      Maximum results to return (default: 5)

    Examples:
      /search-history                    # Recent conversations
      /search-history "error handling"   # Search for specific topic
      /search-history --hours 48 --limit 10
    """

    name = "search-history"
    category = "history"

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext],
        query: str | None = None,
        *,
        hours: int = 24,
        limit: int = 5,
    ) -> None:
        """Search conversation history.

        Args:
            ctx: Command context
            query: Optional search query
            hours: Look back period in hours
            limit: Maximum results to return
        """
        if ctx.context.agent.storage is None:
            raise CommandError("No storage manager available for history search")

        try:
            from agentpool_storage.formatters import format_output

            provider = ctx.context.agent.storage.get_history_provider()
            results = await provider.get_filtered_conversations(
                query=query,
                period=f"{hours}h",
                limit=limit,
            )
            output = format_output(results)
            await ctx.print(f"## Search Results\n\n{output}")
        except Exception as e:
            raise CommandError(f"Failed to search history: {e}") from e
