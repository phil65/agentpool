"""Tool management commands."""

from __future__ import annotations

from slashed import CommandContext, CommandError
from slashed.completers import CallbackCompleter

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool_commands.base import AgentCommand
from agentpool_commands.completers import get_available_agents
from agentpool_commands.markdown_utils import format_table


logger = get_logger(__name__)


class AddWorkerCommand(AgentCommand):
    """Add another agent as a worker tool.

    Add another agent as a worker tool.

    Options:
      --reset-history    Clear worker's history before each run (default: true)
      --share-history   Pass current agent's message history (default: false)
      --share-context   Share context data between agents (default: false)

    Examples:
      /add-worker specialist               # Basic worker
      /add-worker analyst --share-history  # Pass conversation history
      /add-worker helper --share-context   # Share context between agents
    """

    name = "add-worker"
    category = "tools"

    async def execute_command(
        self,
        ctx: CommandContext[AgentContext],
        worker_name: str,
        *,
        reset_history: str = "true",
        share_history: str = "false",
    ) -> None:
        """Add another agent as a worker tool.

        Args:
            ctx: Command context
            worker_name: Name of the agent to add as worker
            reset_history: Clear worker's history before each run
            share_history: Pass current agent's message history
        """
        try:
            if ctx.context.pool is None:
                raise CommandError("No agent pool available")  # noqa: TRY301

            # Get worker agent from pool
            worker = ctx.context.pool.get_agent(worker_name)

            # Parse boolean flags with defaults
            reset_history_bool = reset_history.lower() != "false"
            share_history_bool = share_history.lower() == "true"

            # Register worker
            tool_info = ctx.context.agent.tools.register_worker(
                worker,
                reset_history_on_run=reset_history_bool,
                pass_message_history=share_history_bool,
                parent=ctx.context.native_agent,
            )

            await ctx.print(
                f"✅ **Added agent** `{worker_name}` **as worker tool:** `{tool_info.name}`\n"
                f"🔧 **Tool enabled:** {tool_info.enabled}"
            )

        except KeyError as e:
            raise CommandError(f"Agent not found: {worker_name}") from e
        except Exception as e:
            raise CommandError(f"Failed to add worker: {e}") from e

    def get_completer(self) -> CallbackCompleter:
        """Get completer for agent names."""
        return CallbackCompleter(get_available_agents)


class RemoveWorkerCommand(AgentCommand):
    """Remove a worker tool from the current agent.

    Examples:
      /remove-worker specialist  # Remove the specialist worker tool
    """

    name = "remove-worker"
    category = "tools"

    async def execute_command(
        self,
        ctx: CommandContext[AgentContext],
        worker_name: str,
    ) -> None:
        """Remove a worker tool.

        Args:
            ctx: Command context
            worker_name: Name of the worker to remove
        """
        tool_name = f"ask_{worker_name}"  # Match the naming in to_tool

        try:
            ctx.context.agent.tools.worker_provider.remove_tool(tool_name)
            await ctx.print(f"🗑️ **Removed worker tool:** `{tool_name}`")
        except Exception as e:
            raise CommandError(f"Failed to remove worker: {e}") from e

    def get_completer(self) -> CallbackCompleter:
        """Get completer for agent names."""
        return CallbackCompleter(get_available_agents)


class ListWorkersCommand(AgentCommand):
    """List all registered worker tools and their settings.

    Shows:
    - Worker agent name
    - Tool name
    - Current settings (history/context sharing)
    - Enabled/disabled status

    Example: /list-workers
    """

    name = "list-workers"
    category = "tools"

    async def execute_command(self, ctx: CommandContext[AgentContext]) -> None:
        """List all worker tools.

        Args:
            ctx: Command context
        """
        # Filter tools by source="agent"
        worker_tools = await ctx.context.agent.tools.worker_provider.get_tools()
        if not worker_tools:
            await ctx.print("ℹ️ **No worker tools registered**")  #  noqa: RUF001
            return

        rows = [
            {
                "Status": "✅" if tool_info.enabled else "❌",
                "Agent": tool_info.agent_name,
                "Tool": tool_info.name,
                "Description": tool_info.description or "",
            }
            for tool_info in worker_tools
        ]
        headers = ["Status", "Agent", "Tool", "Description"]
        table = format_table(headers, rows)
        await ctx.print(f"## 👥 Registered Workers\n\n{table}")
