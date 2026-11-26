"""Base command class with node-type filtering support."""

from abc import ABC
from typing import TYPE_CHECKING, Any

from slashed import SlashedCommand


if TYPE_CHECKING:
    from agentpool.messaging import MessageNode


class NodeCommand(SlashedCommand, ABC):
    """Base class for commands with node-type filtering.

    Commands inheriting from this can override `supports_node` to restrict
    which node types they're available for.
    """

    name: str = ""  # Placeholder, must be overridden by concrete subclasses

    @classmethod
    def supports_node(cls, node: MessageNode[Any, Any]) -> bool:
        """Check if this command supports the given node type.

        Override in subclasses to restrict to specific node types.
        Default implementation returns True (supports all nodes).

        Args:
            node: The node to check compatibility with
        """
        return True


class AgentCommand(NodeCommand):
    """Base class for commands that only work with Agent nodes."""

    @classmethod
    def supports_node(cls, node: MessageNode[Any, Any]) -> bool:
        """Check if node is an Agent."""
        from agentpool import Agent

        return isinstance(node, Agent)
