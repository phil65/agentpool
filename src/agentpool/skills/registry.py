"""Claude Code Skills registry with auto-discovery."""

from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar

from fsspec import AbstractFileSystem
from upathtools import is_directory
from upathtools.filesystems.base import BaseAsyncFileSystem, BaseFileSystem, WrapperFileSystem
from upathtools.helpers import to_upath, upath_to_fs

from agentpool.log import get_logger
from agentpool.skills.skill import Skill
from agentpool.tools.exceptions import ToolError
from agentpool.utils.baseregistry import BaseRegistry


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from upathtools import JoinablePathLike, UPath


logger = get_logger(__name__)


class SkillsRegistry(BaseRegistry[str, Skill]):
    """Registry for Claude Code Skills with auto-discovery."""

    DEFAULT_SKILL_PATHS: ClassVar = [
        "~/.claude/skills/",
        "~/.agents/skills/",
        ".claude/skills/",
        ".agents/skills/",
    ]

    def __init__(self, skills_dirs: Sequence[JoinablePathLike] | None = None) -> None:
        """Initialize with custom skill directories or auto-detect."""
        super().__init__()
        if skills_dirs:
            self.skills_dirs = [to_upath(i).expanduser() for i in skills_dirs]
        else:
            self.skills_dirs = [to_upath(i).expanduser() for i in self.DEFAULT_SKILL_PATHS]

    async def discover_skills(self) -> None:
        """Scan filesystem and register all found skills."""
        for skills_dir in self.skills_dirs:
            await self.register_skills_from_path(skills_dir)

    async def register_skills_from_path(
        self,
        skills_dir: JoinablePathLike | AbstractFileSystem,
        base_path: str | None = None,
        **storage_options: Any,
    ) -> None:
        """Register skills from a given path.

        Args:
            skills_dir: Path to the directory containing skills, or filesystem instance.
            base_path: When skills_dir is a filesystem, the path within that filesystem
                      to look for skills. Defaults to root_marker if not specified.
            storage_options: Additional options to pass to the filesystem.
        """
        from upathtools.async_ops import to_async_fs

        skill_path_for: Callable[[str], UPath]
        if isinstance(skills_dir, AbstractFileSystem):
            fs = to_async_fs(skills_dir)
            search_path = base_path if base_path is not None else fs.root_marker
            skill_path_for = partial(_path_on_filesystem, skills_dir)
        else:
            original_skills_dir = to_upath(skills_dir).expanduser()
            fs = upath_to_fs(original_skills_dir, **storage_options)
            search_path = fs.root_marker
            skill_path_for = original_skills_dir.joinpath

        try:
            entries = await fs._ls(search_path, detail=True)
        except FileNotFoundError:
            logger.warning("Skills directory not found", path=search_path)
            return

        skill_dirs = [
            entry
            for entry in entries
            if await is_directory(fs, entry["name"], entry_type=entry.get("type"))
        ]
        if not skill_dirs:
            logger.info("No skills found", skills_dir=search_path)
            return
        logger.info("Found skills", skills=skill_dirs, skills_dir=search_path)
        for skill_entry in skill_dirs:
            entry_name = skill_entry["name"]
            skill_dir_path = skill_path_for(entry_name)

            fs_skill_md_path = f"{entry_name}/SKILL.md"
            try:
                content = await fs._cat_file(fs_skill_md_path)
            except FileNotFoundError:
                continue

            try:
                # Parse the content read above: skill_dir_path may live on an async
                # filesystem (e.g. the ACP client's) that can't be read synchronously.
                skill = Skill.from_skill_md(skill_dir_path, content.decode("utf-8"))
                self.register(skill.name, skill, replace=True)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Failed to parse skill",
                    path=str(skill_dir_path),
                    error=str(e),
                )

    @property
    def _error_class(self) -> type[ToolError]:
        """Error class to use for this registry."""
        return ToolError

    def _validate_item(self, item: Any) -> Skill:
        """Validate and possibly transform item before registration."""
        if not isinstance(item, Skill):
            raise ToolError(f"Expected Skill instance, got {type(item)}")
        return item

    async def get_skill_instructions(self, skill_name: str) -> str:
        """Lazy load full instructions for a skill."""
        skill = self.get(skill_name)
        return await skill.load_instructions()


def _path_on_filesystem(fs: AbstractFileSystem, path: str) -> UPath:
    """Return a UPath for ``path`` that stays bound to ``fs``.

    upathtools filesystems (such as the ACP client filesystem) hand out paths
    bound to the very instance, so later reads go through the same session.
    Other fsspec filesystems get a UPath with their protocol instead of being
    treated as local paths.
    """
    if isinstance(fs, BaseFileSystem | BaseAsyncFileSystem | WrapperFileSystem):
        return fs.get_upath(path)
    return to_upath(fs.unstrip_protocol(path))


if __name__ == "__main__":
    import os

    import anyio
    from upathtools import UPath

    from agentpool.log import configure_logging

    configure_logging()

    async def main() -> None:
        reg = SkillsRegistry()
        p = UPath(
            "github://",
            token=os.getenv("GITHUB_TOKEN"),
            username="phil65",
            org="anthropics",
            repo="skills",
        )
        print("Repository contents:")
        print([f.name for f in p.iterdir()][:5])

        await reg.register_skills_from_path(
            p,
            token=os.getenv("GITHUB_TOKEN"),
            username="phil65",
            org="anthropics",
            repo="skills",
        )
        print(f"Found {len(reg)} skills:")
        for name, skill in reg.items():
            print(f"  - {name}: {skill.description[:60]}...")

    anyio.run(main)
