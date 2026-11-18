"""Provider for skills and commands tools."""

from __future__ import annotations

from acp.filesystem import ACPPath
from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.resource_providers import StaticResourceProvider


BASE_DESC = """Load a Claude Code Skill and return its instructions.

This tool provides access to Claude Code Skills - specialized workflows and techniques
for handling specific types of tasks. When you need to use a skill, call this tool
with the skill name.

Available skills:"""


async def load_skill(ctx: AgentContext, skill_name: str) -> str:
    """Load a Claude Code Skill and return its instructions.

    Args:
        ctx: Agent context providing access to pool and skills
        skill_name: Name of the skill to load

    Returns:
        The full skill instructions for execution
    """
    if ctx.pool is None:
        return "No agent pool available - skills require pool context"

    skills = ctx.pool.skills.list_skills()
    if not skills:
        return "No skills available."
    if skill := next((s for s in skills if s.name == skill_name), None):
        try:
            instructions = ctx.pool.skills.get_skill_instructions(skill_name)
        except Exception as e:  # noqa: BLE001
            return f"Failed to load skill {skill_name!r}: {e}"
        header = f"# {skill.name}\n\n{skill.description}"
        meta_lines: list[str] = []
        if skill.license:
            meta_lines.append(f"License: {skill.license}")
        if skill.compatibility:
            meta_lines.append(f"Compatibility: {skill.compatibility}")
        if skill.allowed_tools:
            meta_lines.append(f"Allowed tools: {skill.allowed_tools}")
        meta = "\n".join(meta_lines)
        parts = [header]
        if meta:
            parts.append(meta)
        parts.append(instructions)
        # Skills from the ACP client live on its filesystem; the session's file
        # tools take plain paths there, not `acp://` URLs.
        skill_dir = (
            skill.skill_path.path if isinstance(skill.skill_path, ACPPath) else skill.skill_path
        )
        parts.append(f"Skill directory: {skill_dir}")
        return "\n\n".join(parts)
    available = ", ".join(s.name for s in skills)
    return f"Skill {skill_name!r} not found. Available skills: {available}"


async def list_skills(ctx: AgentContext) -> str:
    """List all available skills.

    Returns:
        Formatted list of available skills with descriptions
    """
    if ctx.pool is None:
        return "No agent pool available - skills require pool context"
    if skills := ctx.pool.skills.list_skills():
        lines = ["Available skills:", ""]
        lines.extend(f"- **{skill.name}**: {skill.description}" for skill in skills)
        return "\n".join(lines)
    return "No skills available"


class SkillsTools(StaticResourceProvider):
    """Provider for skills and commands tools.

    Provides tools to:
    - Discover and load skills from the pool's skills registry
    - Execute internal commands via the agent's command system

    Skills are discovered from configured directories (e.g., ~/.claude/skills/,
    .claude/skills/).

    Commands provide access to management operations like creating agents,
    managing tools, connecting nodes, etc. Use run_command("/help") to discover
    available commands.
    """

    def __init__(self, name: str = "skills") -> None:
        super().__init__(name=name)
        self._tools = [
            self.create_tool(load_skill, category="read", read_only=True, idempotent=True),
            self.create_tool(list_skills, category="read", read_only=True, idempotent=True),
        ]
