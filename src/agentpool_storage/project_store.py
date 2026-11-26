"""High-level project store with auto-detection."""

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from agentpool.log import get_logger
from agentpool.sessions.models import ProjectData
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from agentpool.storage.manager import StorageManager

logger = get_logger(__name__)

# Config file names to search for in .agentpool/ directory
CONFIG_FILENAMES = ["config.yml", "config.yaml", "config.json", "config.toml"]


def resolve_config(project: ProjectData | None = None, cwd: str | None = None) -> str | None:
    """Resolve config path using project settings and global fallback.

    Resolution order:
    1. Project's explicit config_path (if set)
    2. .agentpool/config.yml in project worktree (auto-discovered)
    3. Global default from ConfigStore (CLI fallback)
    4. None (use built-in defaults)

    Args:
        project: Project data (optional)
        cwd: Current working directory for discovery if no project

    Returns:
        Path to config file, or None if no config found
    """
    from agentpool_cli.store import ConfigStore

    # 1. Project-specific explicit config
    if project and project.config_path:
        config_path = Path(project.config_path)
        if config_path.is_file():
            return str(config_path)
        logger.warning("Project config not found", path=project.config_path)

    # 2. Auto-discover in project worktree
    if worktree := (project.worktree if project else cwd):
        local_config = discover_config_path(worktree)
        if local_config:
            return local_config

    # 3. Global default from ConfigStore
    try:
        config_store = ConfigStore()
        if active := config_store.get_active():
            config_path = Path(active.path)
            if config_path.is_file():
                return str(config_path)
            logger.warning("Active config not found", path=active.path)
    except Exception:
        logger.exception("Error loading ConfigStore")
    # 4. No config found
    return None


def detect_project_root(cwd: str) -> tuple[str, str | None]:
    """Walk up directory tree to find VCS root or use cwd.

    Args:
        cwd: Current working directory

    Returns:
        Tuple of (worktree_path, vcs_type).
        vcs_type is "git", "hg", or None if no VCS found.
    """
    path = Path(cwd).resolve()
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return str(parent), "git"
        if (parent / ".hg").exists():
            return str(parent), "hg"
    # No VCS found, use the original directory
    return str(path), None


def generate_project_id(worktree: str) -> str:
    """Generate stable hash of canonical worktree path.

    Args:
        worktree: Absolute path to project root

    Returns:
        40-character hex string (SHA1 hash)
    """
    canonical = str(Path(worktree).resolve())
    return hashlib.sha1(canonical.encode()).hexdigest()


def discover_config_path(worktree: str) -> str | None:
    """Search for config file in .agentpool/ directory.

    Args:
        worktree: Project root path

    Returns:
        Path to config file if found, None otherwise
    """
    config_dir = Path(worktree) / ".agentpool"
    if not config_dir.is_dir():
        return None

    for filename in CONFIG_FILENAMES:
        config_path = config_dir / filename
        if config_path.is_file():
            return str(config_path)
    return None


class ProjectStore:
    """High-level API for project management.

    Provides:
    - Auto-detection of project root from cwd
    - Auto-creation of projects
    - Config file discovery
    - Convenient lookup methods
    """

    def __init__(self, storage: StorageManager) -> None:
        """Initialize project store."""
        self.storage = storage

    async def get_or_create(self, cwd: str) -> ProjectData:
        """Get existing project or create one for the given directory.

        Detects VCS root, generates project ID, discovers config,
        and creates/updates the project.

        Args:
            cwd: Current working directory

        Returns:
            ProjectData for the detected/created project
        """
        # Detect project root and VCS
        worktree, vcs = detect_project_root(cwd)

        # Check if project already exists
        if existing := await self.storage.get_project_by_worktree(worktree):
            # Update last_active and return
            await self.storage.touch_project(existing.project_id)
            return existing.touch()

        # Create new project
        project_id = generate_project_id(worktree)
        config_path = discover_config_path(worktree)

        project = ProjectData(
            project_id=project_id,
            worktree=worktree,
            name=None,  # Can be set later
            vcs=vcs,
            config_path=config_path,
            created_at=get_now(),
            last_active=get_now(),
        )

        await self.storage.save_project(project)
        logger.info(
            "Created project",
            project_id=project_id,
            worktree=worktree,
            vcs=vcs,
            config_path=config_path,
        )
        return project

    async def get_by_cwd(self, cwd: str) -> ProjectData | None:
        """Get project for the given directory without creating.

        Args:
            cwd: Current working directory

        Returns:
            ProjectData if found, None otherwise
        """
        worktree, _ = detect_project_root(cwd)
        return await self.storage.get_project_by_worktree(worktree)

    async def get_by_name(self, name: str) -> ProjectData | None:
        """Get project by friendly name.

        Args:
            name: Project name

        Returns:
            ProjectData if found, None otherwise
        """
        return await self.storage.get_project_by_name(name)

    async def get_by_id(self, project_id: str) -> ProjectData | None:
        """Get project by ID.

        Args:
            project_id: Project identifier

        Returns:
            ProjectData if found, None otherwise
        """
        return await self.storage.get_project(project_id)

    async def list_recent(self, limit: int = 20) -> list[ProjectData]:
        """List recent projects ordered by last_active.

        Args:
            limit: Maximum number of projects to return

        Returns:
            List of projects
        """
        projects = await self.storage.list_projects(limit=limit)
        return projects if projects is not None else []

    async def set_name(self, project_id: str, name: str) -> ProjectData | None:
        """Set friendly name for a project.

        Args:
            project_id: Project identifier
            name: New name for the project

        Returns:
            Updated ProjectData, or None if project not found
        """
        project = await self.storage.get_project(project_id)
        if not project:
            return None

        updated = project.model_copy(update={"name": name, "last_active": get_now()})
        await self.storage.save_project(updated)
        return updated

    async def set_config_path(self, project_id: str, config_path: str | None) -> ProjectData | None:
        """Set or clear config path for a project.

        Args:
            project_id: Project identifier
            config_path: Path to config file, or None to use auto-discovery

        Returns:
            Updated ProjectData, or None if project not found
        """
        project = await self.storage.get_project(project_id)
        if not project:
            return None

        updated = project.model_copy(update={"config_path": config_path, "last_active": get_now()})
        await self.storage.save_project(updated)
        return updated

    async def update_settings(self, project_id: str, **settings: object) -> ProjectData | None:
        """Update project-specific settings.

        Args:
            project_id: Project identifier
            **settings: Settings to update

        Returns:
            Updated ProjectData, or None if project not found
        """
        project = await self.storage.get_project(project_id)
        if not project:
            return None

        updated = project.with_settings(**settings)
        await self.storage.save_project(updated)
        return updated

    async def delete(self, project_id: str) -> bool:
        """Delete a project.

        Args:
            project_id: Project identifier

        Returns:
            True if deleted, False if not found
        """
        result = await self.storage.delete_project(project_id)
        return bool(result)

    async def refresh_config(self, project_id: str) -> ProjectData | None:
        """Re-discover config file for a project.

        Useful after user adds .agentpool/config.yml to their project.

        Args:
            project_id: Project identifier

        Returns:
            Updated ProjectData, or None if project not found
        """
        project = await self.storage.get_project(project_id)
        if not project:
            return None

        config_path = discover_config_path(project.worktree)
        if config_path != project.config_path:
            update = {"config_path": config_path, "last_active": get_now()}
            updated = project.model_copy(update=update)
            await self.storage.save_project(updated)
            return updated
        return project
