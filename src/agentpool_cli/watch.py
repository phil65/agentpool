"""Command for watching agents and displaying messages."""

import asyncio
from typing import TYPE_CHECKING, Annotated, Any

import typer as t

from agentpool_cli import log
from agentpool_cli.cli_types import DetailLevel  # noqa: TC001


if TYPE_CHECKING:
    from agentpool import ChatMessage


logger = log.get_logger(__name__)


def watch_command(
    config: Annotated[str, t.Argument(help="Path to agent configuration")],
    show_messages: Annotated[
        bool, t.Option("--show-messages", help="Show all messages (not just final responses)")
    ] = True,
    detail_level: Annotated[
        DetailLevel, t.Option("-d", "--detail", help="Output detail level")
    ] = "simple",
    show_metadata: Annotated[bool, t.Option("--metadata", help="Show message metadata")] = False,
    show_costs: Annotated[bool, t.Option("--costs", help="Show token usage and costs")] = False,
) -> None:
    """Run agents in event-watching mode."""

    def on_message(chat_message: ChatMessage[Any]) -> None:
        text = chat_message.format(
            style=detail_level,
            show_metadata=show_metadata,
            show_costs=show_costs,
        )
        print(text)

    async def run_watch() -> None:
        from agentpool import AgentPool, AgentsManifest

        manifest = AgentsManifest.from_file(config)
        async with AgentPool(manifest) as pool:
            # Connect message handlers if showing all messages
            if show_messages:
                for agent in pool.all_agents.values():
                    agent.message_sent.connect(on_message)

            await pool.run_event_loop()

    asyncio.run(run_watch())
