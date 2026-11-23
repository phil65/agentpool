"""Resource provider exposing Nodes as tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool import AgentPool, MessageNode
    from agentpool.resource_providers.resource_info import ResourceInfo
    from agentpool.tools import FunctionTool

logger = get_logger(__name__)


class PoolResourceProvider(ResourceProvider):
    """Provider that exposes an AgentPool's resources."""

    kind = "tools"

    def __init__(
        self,
        pool: AgentPool[Any],
        name: str | None = None,
        include_team_members: bool = False,
        nodes: Sequence[str] | None = None,
    ) -> None:
        """Initialize provider with agent pool.

        Args:
            pool: Agent pool to expose resources from
            name: Optional name override (defaults to pool name)
            include_team_members: Whether to include team members in the pool
                                  in addition to the team itself.
            nodes: Exactly these nodes (agents or teams, team members too), instead of
                   all teams and the agents outside them
        """
        super().__init__(name=name or repr(pool))
        self.pool = pool
        self.include_team_members = include_team_members
        self.nodes = list(nodes) if nodes is not None else None

    def served_nodes(self) -> list[MessageNode[Any, Any]]:
        """The nodes served: ``nodes``, else all teams and the agents outside them."""
        if self.nodes is not None:
            if unknown := [name for name in self.nodes if name not in self.pool.nodes]:
                msg = f"Unknown nodes to serve: {unknown} (pool has {sorted(self.pool.nodes)})"
                raise ValueError(msg)
            return [self.pool.nodes[name] for name in self.nodes]
        teams: list[MessageNode[Any, Any]] = list(self.pool.teams.values())
        agents = list(self.pool.get_agents().values())
        if self.include_team_members:
            return [*teams, *agents]
        members = {member for team in self.pool.teams.values() for member in team.nodes}
        return [*teams, *(agent for agent in agents if agent not in members)]

    async def get_tools(self) -> Sequence[FunctionTool]:
        """Get a tool per served node (see :meth:`served_nodes`)."""
        return [node.to_tool() for node in self.served_nodes()]

    async def get_resources(self) -> list[ResourceInfo]:
        """Get resources from pool's manifest."""
        # Here we could expose knowledge bases or other resources from manifest
        return []
