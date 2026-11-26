"""Serve the pool's skills through the MCP Skills extension (SEP-2640)."""

from typing import TYPE_CHECKING

from fastmcp.skills import Skill as FastMCPSkill
from fastmcp.skills.provider import SkillCatalogProvider

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Mapping

    from agentpool.skills.manager import SkillsManager
    from agentpool.skills.skill import Skill


logger = get_logger(__name__)


class PoolSkillsProvider(SkillCatalogProvider):
    """FastMCP skill provider serving the skills of an agent pool.

    Every file of a skill is read through the skill's own filesystem, so skills
    from remote or client filesystems are served like local ones. Converted
    skills are cached per skill instance: a registry refresh replaces the
    instances, which invalidates their cache entries. Skills FastMCP rejects
    (for example over the 512-file / 16 MiB limits) are skipped with a warning.
    """

    def __init__(self, skills: SkillsManager) -> None:
        """Initialize the provider.

        Args:
            skills: The pool's skills manager.
        """
        super().__init__()
        self._skills = skills
        self._converted: dict[int, tuple[Skill, FastMCPSkill | None]] = {}

    async def load_skills(self) -> Mapping[str, FastMCPSkill]:
        """Return the pool's skills, converted to FastMCP skills, keyed by name."""
        current = self._skills.list_skills()
        live_ids = {id(skill) for skill in current}
        self._converted = {k: v for k, v in self._converted.items() if k in live_ids}
        catalog: dict[str, FastMCPSkill] = {}
        for skill in current:
            cached = self._converted.get(id(skill))
            if cached is None or cached[0] is not skill:
                cached = (skill, await _convert(skill))
                self._converted[id(skill)] = cached
            if (converted := cached[1]) is not None:
                catalog[skill.name] = converted
        return catalog


async def _convert(skill: Skill) -> FastMCPSkill | None:
    """Read every file of ``skill`` and build a FastMCP skill, or None if it is rejected."""
    try:
        files = {path: await skill.read_file(path) for path in await skill.list_files()}
        return FastMCPSkill.from_files(files)
    except (OSError, ValueError) as e:
        logger.warning("Not serving skill over MCP", skill=skill.name, error=str(e))
        return None
