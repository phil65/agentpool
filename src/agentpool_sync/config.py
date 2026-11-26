"""Configuration models for sync settings.

These models can be embedded in the main agent manifest or used standalone.
"""

from pydantic import BaseModel, Field


class PackageSyncConfig(BaseModel):
    """Configuration for tracking package versions."""

    packages: list[str] = Field(default_factory=list)
    """Package names to track for version changes."""

    agent: str | None = None
    """Agent to use for package reconciliation."""

    fetch_release_notes: bool = True
    """Whether to fetch release notes when changes detected."""

    ignore_patch_versions: bool = True
    """Only trigger on minor/major version bumps."""


class UrlSyncConfig(BaseModel):
    """Configuration for a tracked URL resource."""

    url: str
    """The URL to track."""

    description: str | None = None
    """Optional description of what this URL provides."""


class FileSyncConfig(BaseModel):
    """Configuration for file-level sync (defined in-file, this is for reference)."""

    agent: str | None = None
    """Reference to agent name that handles reconciliation."""

    dependencies: list[str] = Field(default_factory=list)
    """Glob patterns for file dependencies."""

    urls: list[str] = Field(default_factory=list)
    """URL dependencies."""

    context: dict[str, str] = Field(default_factory=dict)
    """Additional context passed to the agent."""


class SyncConfig(BaseModel):
    """Root sync configuration.

    Can be embedded in AgentsManifest or used standalone:

        # In agents.yml
        agents:
          doc_sync_agent:
            model: openai:gpt-4o
            system_prompt: "You maintain documentation..."

        sync:
          default_agent: doc_sync_agent
          packages:
            packages: ["pydantic", "sqlmodel"]
            agent: package_sync_agent
          urls:
            - url: "https://docs.pydantic.dev/api/"
              description: "Pydantic API docs"
    """

    default_agent: str | None = None
    """Default agent for file sync if not specified in frontmatter."""

    packages: PackageSyncConfig = Field(default_factory=PackageSyncConfig)
    """Package tracking configuration."""

    urls: list[UrlSyncConfig] = Field(default_factory=list)
    """URL resources to track centrally."""

    registry_path: str = ".agentpool/resources.yml"
    """Path to the resource registry file."""

    packages_registry_path: str = ".agentpool/packages.yml"
    """Path to the packages registry file."""
