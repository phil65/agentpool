"""Provider for skills tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import ModelRetry

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.log import get_logger
from agentpool.resource_providers import StaticResourceProvider
from agentpool.skills.skill import SkillVerificationError
from agentpool.tools.exceptions import ToolError


if TYPE_CHECKING:
    from agentpool.skills.skill import Skill


logger = get_logger(__name__)

MAX_READ_CHARS = 50_000
"""Most characters ``read_skill_file`` returns per call."""
MAX_FILE_BYTES = 10 * 1024 * 1024
"""Largest skill file ``read_skill_file`` reads."""


async def collect_skills(ctx: AgentContext) -> dict[str, Skill]:
    """Collect the skills visible to the calling agent, keyed by qualified name.

    These are the pool's skills (addressed by bare name) and the skills of the
    agent's resource providers, such as its MCP servers (addressed as
    ``<server>/<name>``). A qualified name is never reassigned: when two
    providers publish the same one, the later skill is skipped with a warning,
    so no source can shadow another's skill.

    Args:
        ctx: Agent context of the tool call.

    Returns:
        Mapping of qualified name to skill, in discovery order.
    """
    catalog: dict[str, Skill] = {}
    if ctx.pool is not None:
        for skill in ctx.pool.skills.list_skills():
            catalog[skill.qualified_name] = skill
    for provider in ctx.agent.tools.external_providers:
        for skill in await provider.get_skills():
            key = skill.qualified_name
            if (existing := catalog.get(key)) is not None and existing is not skill:
                logger.warning(
                    "Skipping skill whose qualified name is already taken; "
                    "set a distinct `name` on the MCP server config",
                    skill=key,
                    provider=provider.name,
                )
                continue
            catalog[key] = skill
    return catalog


async def _resolve(ctx: AgentContext, skill_name: str) -> Skill:
    catalog = await collect_skills(ctx)
    skill = catalog.get(skill_name)
    if skill is None or skill.disable_model_invocation:
        available = ", ".join(k for k, s in catalog.items() if not s.disable_model_invocation)
        raise ModelRetry(
            f"Skill {skill_name!r} not found. Available skills: {available or '(none)'}"
        )
    return skill


async def load_skill(ctx: AgentContext, skill_name: str) -> str:
    """Load a Claude Code Skill and return its instructions.

    Args:
        ctx: Agent context providing access to pool and skills
        skill_name: Name of the skill to load, as listed by list_skills

    Returns:
        The full skill instructions for execution
    """
    skill = await _resolve(ctx, skill_name)
    if ignored := skill.ignored_behavioral_fields:
        logger.warning(
            "Ignoring unsupported skill frontmatter fields",
            skill=skill.qualified_name,
            fields=ignored,
        )
    try:
        return await skill.render()
    except SkillVerificationError as e:
        raise ToolError(f"Skill {skill_name!r} failed verification: {e}") from e
    except (OSError, ValueError, RuntimeError) as e:
        raise ToolError(f"Failed to load skill {skill_name!r}: {e}") from e


async def list_skills(ctx: AgentContext) -> str:
    """List all available skills.

    Returns:
        Formatted list of available skills with descriptions
    """
    catalog = await collect_skills(ctx)
    visible = [s for s in catalog.values() if not s.disable_model_invocation]
    if not visible:
        return "No skills available"
    lines = ["Available skills:", ""]
    lines.extend(f"- **{s.qualified_name}**: {s.catalog_description}" for s in visible)
    return "\n".join(lines)


async def read_skill_file(
    ctx: AgentContext,
    skill_name: str,
    path: str,
    offset: int = 0,
) -> str:
    """Read a text file bundled with a skill, such as references/guide.md.

    Args:
        ctx: Agent context providing access to pool and skills
        skill_name: Name of the skill, as listed by list_skills
        path: Path relative to the skill directory, without the directory itself
        offset: Character to start at, to continue a file longer than one read returns

    Returns:
        The file's text, or a window of it with a hint how to read on
    """
    if offset < 0:
        raise ModelRetry("offset must not be negative")
    skill = await _resolve(ctx, skill_name)
    try:
        content = await skill.read_file(path)
    except ValueError as e:
        if isinstance(e, SkillVerificationError):
            raise ToolError(f"{path!r} in skill {skill_name!r} failed verification: {e}") from e
        raise ModelRetry(str(e)) from e
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as e:
        raise ModelRetry(f"Skill {skill_name!r} has no file {path!r}") from e
    except PermissionError as e:
        raise ToolError(f"{path!r} in skill {skill_name!r} cannot be read: {e}") from e
    if len(content) > MAX_FILE_BYTES:
        raise ToolError(
            f"{path!r} in skill {skill_name!r} is {len(content):,} bytes, "
            f"over the {MAX_FILE_BYTES:,}-byte limit"
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ModelRetry(f"{path!r} in skill {skill_name!r} is not a UTF-8 text file") from e
    if offset and offset >= len(text):
        raise ModelRetry(f"offset={offset} is past the end of {path!r} ({len(text):,} characters)")
    end = offset + MAX_READ_CHARS
    window = text[offset:end]
    if end >= len(text):
        return window
    hint = f"Characters {offset:,} to {end:,} of {len(text):,}; read on from offset={end}."
    return f"{window}\n\n[{hint}]"


class SkillsTools(StaticResourceProvider):
    """Provider for skills tools.

    Provides tools to list skills, load a skill's instructions, and read the
    files bundled with a skill. Skills come from the pool's skills registry
    (discovered from directories such as ``~/.claude/skills/`` and
    ``.agents/skills/``) and from the calling agent's MCP servers that publish
    skills through the MCP Skills extension.
    """

    def __init__(self, name: str = "skills") -> None:
        super().__init__(name=name)
        self._tools = [
            self.create_tool(load_skill, category="read", read_only=True, idempotent=True),
            self.create_tool(list_skills, category="read", read_only=True, idempotent=True),
            self.create_tool(read_skill_file, category="read", read_only=True, idempotent=True),
        ]
