"""Session data models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field
from schemez import Schema

from agentpool.utils.time_utils import get_now


class ProjectData(Schema):
    """Persistable project/worktree state.

    Represents a codebase/worktree that agentpool operates on.
    Sessions are associated with projects.
    """

    project_id: str
    """Unique identifier (hash of canonical worktree path)."""

    worktree: str
    """Absolute path to the project root/worktree."""

    name: str | None = None
    """Optional friendly name for the project."""

    vcs: str | None = None
    """Version control system type ('git', 'hg', or None)."""

    config_path: str | None = None
    """Path to the project's config file, or None for auto-discovery."""

    created_at: datetime = Field(default_factory=get_now)
    """When the project was first registered."""

    last_active: datetime = Field(default_factory=get_now)
    """Last activity timestamp."""

    settings: dict[str, Any] = Field(default_factory=dict)
    """Project-specific settings overrides."""

    def touch(self) -> ProjectData:
        """Return copy with updated last_active timestamp."""
        return self.model_copy(update={"last_active": get_now()})

    def with_settings(self, **kwargs: Any) -> ProjectData:
        """Return copy with updated settings."""
        new_settings = {**self.settings, **kwargs}
        return self.model_copy(update={"settings": new_settings, "last_active": get_now()})


class SessionData(Schema):
    """Persistable session state.

    Contains all information needed to persist and restore a session.
    Protocol-specific data (ACP capabilities, web cookies, etc.) goes in metadata.
    """

    session_id: str
    """Unique session identifier. Also used as session_id for message storage."""

    agent_name: str
    """Name of the currently active agent."""

    pool_id: str | None = None
    """Optional pool/manifest identifier for multi-pool setups."""

    project_id: str | None = None
    """Project identifier (e.g., for OpenCode compatibility)."""

    parent_id: str | None = None
    """Parent session ID for forked sessions."""

    version: str = "1"
    """Session version string."""

    cwd: str | None = None
    """Working directory for the session."""

    agent_type: str | None = None
    """Type of agent backend (native, claude, codex, acp, agui)."""

    sdk_session_id: str | None = None
    """External SDK session ID for cross-referencing (e.g. Claude JSONL stem, Codex thread ID)."""

    definition_version: str | None = None
    """Version of the agent definition the session last ran with (``AgentDefinition``).

    None for agents without a definition (constructed in Python).
    """

    created_at: datetime = Field(default_factory=get_now)
    """When the session was created."""

    last_active: datetime = Field(default_factory=get_now)
    """Last activity timestamp."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Protocol-specific or custom metadata.

    Examples:
        - ACP: client_capabilities, mcp_servers
        - Web: user_id, auth_token
        - CLI: terminal_size, color_support
    """

    def touch(self) -> None:
        """Update last_active timestamp."""
        # Note: Schema is frozen by default, so we need to work around that
        # by using object.__setattr__ or making this field mutable
        object.__setattr__(self, "last_active", get_now())

    def with_agent(self, agent_name: str) -> SessionData:
        """Return copy with different agent."""
        return self.model_copy(update={"agent_name": agent_name, "last_active": get_now()})

    def with_metadata(self, **kwargs: Any) -> SessionData:
        """Return copy with updated metadata."""
        new_metadata = {**self.metadata, **kwargs}
        return self.model_copy(update={"metadata": new_metadata, "last_active": get_now()})

    @property
    def title(self) -> str | None:
        """Human-readable title (from metadata, for protocol compatibility)."""
        return self.metadata.get("title")

    @property
    def updated_at(self) -> str | None:
        """ISO timestamp of last activity (for protocol compatibility)."""
        return self.last_active.isoformat() if self.last_active else None
