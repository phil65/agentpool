"""Skills manager for pool-wide management."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, overload

from upathtools import to_upath

from agentpool.log import get_logger
from agentpool.skills.registry import SkillsRegistry


if TYPE_CHECKING:
    from fsspec import AbstractFileSystem
    from upathtools import JoinablePathLike

    from agentpool.skills.skill import Skill


logger = get_logger(__name__)


class SkillsManager:
    """Pool-wide skills registry management.

    Owns the single skills registry for the pool. Skills can be discovered
    from multiple directories. The actual `load_skill` tool is provided
    separately via SkillsTools toolset.
    """

    def __init__(
        self,
        name: str = "skills",
        owner: str | None = None,
        skills_dirs: list[JoinablePathLike] | None = None,
    ) -> None:
        """Initialize the skills manager.

        Args:
            name: Name for this manager
            owner: Owner of this manager
            skills_dirs: Directories to search for skills
        """
        self.name = name
        self.owner = owner
        self.registry = SkillsRegistry(skills_dirs)
        self._initialized = False

    def __repr__(self) -> str:
        skill_count = len(self.registry.list_items()) if self._initialized else "?"
        return f"SkillsManager(name={self.name!r}, skills={skill_count})"

    async def __aenter__(self) -> Self:
        """Initialize the skills manager and discover skills."""
        try:
            await self.registry.discover_skills()
            self._initialized = True
            count = len(self.registry.list_items())
            logger.info("Skills manager initialized", name=self.name, skill_count=count)
        except Exception as e:
            msg = "Failed to initialize skills manager"
            logger.exception(msg, name=self.name, error=e)
            raise
        return self

    async def __aexit__(self, *args: object) -> None:
        """Clean up the skills manager."""
        # Skills are file-based, no persistent connections to clean up

    @overload
    async def add_skills_directory(self, path: JoinablePathLike) -> None: ...

    @overload
    async def add_skills_directory(self, path: str, *, fs: AbstractFileSystem) -> None: ...

    async def add_skills_directory(
        self,
        path: JoinablePathLike,
        *,
        fs: AbstractFileSystem | None = None,
    ) -> None:
        """Add a new skills directory and discover its skills.

        Args:
            path: Path to directory containing skills.
            fs: Optional filesystem instance. When provided, path is interpreted
                as a path within this filesystem (e.g., ".claude/skills").
        """
        if fs is not None:
            # Pass filesystem directly to registry with the path
            await self.registry.register_skills_from_path(fs, base_path=str(path))
            logger.info(
                "Added skills directory from filesystem",
                protocol=fs.protocol,
                path=path,
            )
        else:
            upath = to_upath(path)
            if upath not in self.registry.skills_dirs:
                self.registry.skills_dirs.append(upath)
                await self.registry.register_skills_from_path(upath)
                logger.info("Added skills directory", path=str(path))

    async def refresh(self) -> None:
        """Force rediscovery of all skills."""
        await self.registry.discover_skills()
        skill_count = len(self.registry.list_items())
        logger.info("Skills refreshed", name=self.name, skill_count=skill_count)

    def list_skills(self) -> list[Skill]:
        """Get all available skills."""
        return [self.registry.get(name) for name in self.registry.list_items()]

    def get_skill(self, name: str) -> Skill:
        """Get a skill by name."""
        return self.registry.get(name)

    async def get_skill_instructions(self, skill_name: str) -> str:
        """Get full instructions for a specific skill."""
        return await self.registry.get_skill_instructions(skill_name)
