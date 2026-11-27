"""Agent delegation and collaboration functionality."""

__lazy_modules__ = [
    "agentpool.delegation.base_team",
    "agentpool.delegation.pool",
    "agentpool.delegation.team",
    "agentpool.delegation.teamrun",
]

from agentpool.delegation.pool import AgentPool
from agentpool.delegation.base_team import BaseTeam
from agentpool.delegation.teamrun import TeamRun
from agentpool.delegation.team import Team

__all__ = ["AgentPool", "BaseTeam", "Team", "TeamRun"]
