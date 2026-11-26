"""Run command for agent execution."""

import asyncio
import traceback
from typing import TYPE_CHECKING, Annotated, Any

import typer as t

from agentpool_cli import resolve_agent_config
from agentpool_cli.cli_types import DetailLevel  # noqa: TC001
from agentpool_cli.common import verbose_opt


if TYPE_CHECKING:
    from agentpool import ChatMessage


def run_command(
    node_name: Annotated[str, t.Argument(help="Agent / Team name to run")],
    prompts: Annotated[list[str] | None, t.Argument(help="Additional prompts to send")] = None,
    config_path: Annotated[
        str | None, t.Option("-c", "--config", help="Override config path")
    ] = None,
    show_messages: Annotated[
        bool, t.Option("--show-messages", help="Show all messages (not just final responses)")
    ] = True,
    detail_level: Annotated[
        DetailLevel, t.Option("-d", "--detail", help="Output detail level")
    ] = "simple",
    show_metadata: Annotated[bool, t.Option("--metadata", help="Show message metadata")] = False,
    show_costs: Annotated[bool, t.Option("--costs", help="Show token usage and costs")] = False,
    verbose: bool = verbose_opt,
) -> None:
    """Single-shot run a node (agent/team) with prompts."""
    try:
        # Resolve configuration path
        try:
            config_path = resolve_agent_config(config_path)
        except ValueError as e:
            error_msg = str(e)
            raise t.BadParameter(error_msg) from e

        async def run() -> None:
            from agentpool import AgentPool

            async with AgentPool(config_path) as pool:

                def on_message(chat_message: ChatMessage[Any]) -> None:
                    print(
                        chat_message.format(
                            style=detail_level,
                            show_metadata=show_metadata,
                            show_costs=show_costs,
                        )
                    )

                # Connect message handlers if showing all messages
                if show_messages:
                    for node in pool.nodes.values():
                        node.message_sent.connect(on_message)
                for prompt in prompts or []:
                    response = await pool.nodes[node_name].run(prompt)

                    if not show_messages:
                        print(
                            response.format(
                                style=detail_level,
                                show_metadata=show_metadata,
                                show_costs=show_costs,
                            )
                        )

        # Run the async code in the sync command
        asyncio.run(run())

    except t.Exit:
        raise
    except Exception as e:
        t.echo(f"Error: {e}", err=True)
        if verbose:
            t.echo(traceback.format_exc(), err=True)
        raise t.Exit(1) from e
