"""Resource provider exposing Nodes as tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool import AgentPool
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
    ) -> None:
        """Initialize provider with agent pool.

        Args:
            pool: Agent pool to expose resources from
            name: Optional name override (defaults to pool name)
            include_team_members: Whether to include team members in the pool
                                  in addition to the team itself.
        """
        super().__init__(name=name or repr(pool))
        self.pool = pool
        self.include_team_members = include_team_members

    async def get_tools(self) -> Sequence[FunctionTool]:
        """Get tools from all agents in pool."""
        team_tools = [team.to_tool() for team in self.pool.teams.values()]
        agents = list(self.pool.get_agents().values())
        team_members = {member for t in self.pool.teams.values() for member in t.nodes}
        if self.include_team_members:
            agent_tools = [agent.to_tool() for agent in agents]
        else:
            agent_tools = [agent.to_tool() for agent in agents if agent not in team_members]
        return team_tools + agent_tools

    async def get_resources(self) -> list[ResourceInfo]:
        """Get resources from pool's manifest."""
        # Here we could expose knowledge bases or other resources from manifest
        return []
