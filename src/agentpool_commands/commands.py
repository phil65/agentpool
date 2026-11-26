"""Slashed command to list nodes."""

from slashed import CommandContext  # noqa: TC002

from agentpool.log import get_logger
from agentpool.messaging.context import NodeContext  # noqa: TC001
from agentpool_commands.base import NodeCommand
from agentpool_commands.markdown_utils import format_table


logger = get_logger(__name__)


class ListNodesCommand(NodeCommand):
    """List all nodes in the pool with their status."""

    name = "list-nodes"
    category = "pool"

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext],
        show_connections: bool = False,
    ) -> None:
        """List all nodes and their current status.

        Args:
            ctx: Command context with node
            show_connections: Whether to show node connections
        """
        node = ctx.get_data()
        assert node.pool

        rows = []
        for name, node_ in node.pool.nodes.items():
            # Status check
            status = "🔄 busy" if node_.task_manager.is_busy() else "⏳ idle"

            # Add connections if requested
            connections = []
            if show_connections and node_.connections.get_targets():
                connections = [a.name for a in node_.connections.get_targets()]
                conn_str = f"→ {', '.join(connections)}"
            else:
                conn_str = ""

            rows.append({
                "Node": name,
                "Status": status,
                "Connections": conn_str,
                "Description": node_.description or "",
            })

        headers = ["Node", "Status", "Connections", "Description"]
        table = format_table(headers, rows)
        await ctx.print(f"## 🔗 Available Nodes\n\n{table}")
