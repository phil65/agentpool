"""Immutable snapshots of MCP server configurations across lifecycle scopes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal


if TYPE_CHECKING:
    from wolfharness_config.mcp_server import MCPServerConfig

SourceScope = Literal["pool", "agent", "session", "skill"]


@dataclass(frozen=True)
class McpConfigEntry:
    """A single MCP server configuration tagged with its lifecycle source.

    Attributes:
        server_config: The MCP server configuration object.
        source: Which lifecycle scope this config originated from.
        skill_name: Name of the skill that contributed this config, if source is "skill".
    """

    server_config: MCPServerConfig
    source: SourceScope
    skill_name: str | None = None


@dataclass(frozen=True)
class McpConfigSnapshot:
    """Immutable, point-in-time view of all MCP server configurations.

    Configurations are partitioned by lifecycle scope so that providers can
    determine which configs are global (shared across sessions) versus
    session-scoped (must be created per-session).

    Attributes:
        pool_configs: MCP servers declared at the pool level.
        agent_configs: MCP servers declared on a specific agent.
        session_configs: MCP servers injected at session creation time.
        skill_configs: MCP servers contributed by loaded skills.
    """

    pool_configs: tuple[McpConfigEntry, ...] = ()
    agent_configs: tuple[McpConfigEntry, ...] = ()
    session_configs: tuple[McpConfigEntry, ...] = ()
    skill_configs: tuple[McpConfigEntry, ...] = ()

    @property
    def all_configs(self) -> tuple[McpConfigEntry, ...]:
        """All config entries from every scope, in canonical order."""
        return self.pool_configs + self.agent_configs + self.session_configs + self.skill_configs

    @property
    def global_configs(self) -> tuple[McpConfigEntry, ...]:
        """Configs that are shared across sessions (pool + agent)."""
        return self.pool_configs + self.agent_configs

    @property
    def session_scoped_configs(self) -> tuple[McpConfigEntry, ...]:
        """Configs that are specific to a single session (session + skill)."""
        return self.session_configs + self.skill_configs

    def with_skill_configs(self, skills: tuple[McpConfigEntry, ...]) -> McpConfigSnapshot:
        """Return a new snapshot with skill configs replaced.

        Args:
            skills: New skill-scoped config entries.

        Returns:
            A new frozen ``McpConfigSnapshot`` instance.
        """
        return McpConfigSnapshot(
            pool_configs=self.pool_configs,
            agent_configs=self.agent_configs,
            session_configs=self.session_configs,
            skill_configs=skills,
        )

    def with_session_configs(self, sessions: tuple[McpConfigEntry, ...]) -> McpConfigSnapshot:
        """Return a new snapshot with session configs replaced.

        Args:
            sessions: New session-scoped config entries.

        Returns:
            A new frozen ``McpConfigSnapshot`` instance.
        """
        return McpConfigSnapshot(
            pool_configs=self.pool_configs,
            agent_configs=self.agent_configs,
            session_configs=sessions,
            skill_configs=self.skill_configs,
        )
