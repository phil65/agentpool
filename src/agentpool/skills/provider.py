"""Skills of one workspace, as a resource provider (a session's client-side skills)."""

from typing import TYPE_CHECKING

from upathtools import to_upath

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider
from agentpool.skills.registry import SkillsRegistry


if TYPE_CHECKING:
    from fsspec import AbstractFileSystem
    from upathtools import JoinablePathLike

    from agentpool.skills.skill import Skill


logger = get_logger(__name__)


class WorkspaceSkillsProvider(ResourceProvider):
    """Skills found in a workspace, offered to the turns of the session it belongs to.

    Unlike the pool's skills (``SkillsManager``), they are not shared: a session puts
    the provider into its environment (``SessionEnvironment.providers``), so sessions
    of other workspaces don't see them. Its skills win over pool skills of the same name.
    """

    kind = "skills"

    def __init__(self, name: str = "workspace_skills", owner: str | None = None) -> None:
        super().__init__(name, owner=owner)
        self.registry = SkillsRegistry()

    async def add_skills_directory(
        self, path: JoinablePathLike, *, fs: AbstractFileSystem | None = None
    ) -> None:
        """Discover the skills in ``path`` (on ``fs`` if given, e.g. the client's)."""
        if fs is not None:
            await self.registry.register_skills_from_path(fs, base_path=str(path))
        else:
            await self.registry.register_skills_from_path(to_upath(path))
        await self.skills_changed.emit(self.create_change_event("skills"))

    async def get_skills(self) -> list[Skill]:
        return [self.registry.get(name) for name in self.registry.list_items()]

    async def get_skill_instructions(self, skill_name: str) -> str:
        return await self.registry.get_skill_instructions(skill_name)
