"""Claude Code Skill following the Agent Skills Spec."""

from __future__ import annotations

import html
import json
import os
import posixpath
import re
from typing import TYPE_CHECKING, Annotated, Any, ClassVar
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from upathtools import UPath

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Mapping

    from fsspec.asyn import AsyncFileSystem


logger = get_logger(__name__)

# Last synced with https://github.com/agentskills/agentskills
SPEC_SYNCED_COMMIT = "f019a02dbbb1302217c4b4a14557d6384d9ace9a"

MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_COMPATIBILITY_LENGTH = 500
MAX_LISTED_RESOURCES = 100
"""Most bundled files ``Skill.render`` lists before noting the list is incomplete."""

SKILL_FILE = "SKILL.md"

BEHAVIORAL_FRONTMATTER_FIELDS = frozenset({
    "agent",
    "allowed-tools",
    "argument-hint",
    "arguments",
    "context",
    "dependencies",
    "disallowed-tools",
    "effort",
    "hooks",
    "model",
    "paths",
    "shell",
    "tools",
})
"""Frontmatter fields that change invocation, permissions or execution in other clients.

AgentPool accepts skills using them but does not implement their behavior.
"""

_SKILL_DIR_PLACEHOLDER = re.compile(r"\$\{(?:CLAUDE_)?SKILL_DIR\}")
_LOCAL_PROTOCOLS = frozenset({"", "file", "local"})


class SkillVerificationError(ValueError):
    """A skill file does not match what its source promised (size, digest or frontmatter)."""


class Skill(BaseModel):
    """A Claude Code Skill with metadata and lazy-loaded instructions.

    Follows the Agent Skills Spec for field naming and validation rules. The model
    itself rejects unknown constructor arguments; unknown frontmatter keys are kept
    in ``extra_frontmatter`` by ``from_frontmatter``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    FRONTMATTER_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "name",
        "description",
        "license",
        "compatibility",
        "allowed-tools",
        "metadata",
        "when_to_use",
        "disable-model-invocation",
        "user-invocable",
    })
    """Frontmatter keys mapped to fields of this model (by their frontmatter spelling)."""

    name: Annotated[str, Field(max_length=MAX_SKILL_NAME_LENGTH)]
    description: Annotated[str, Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)]
    skill_path: UPath
    license: str | None = None
    compatibility: Annotated[
        str | None, Field(min_length=1, max_length=MAX_COMPATIBILITY_LENGTH)
    ] = None
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")
    metadata: dict[str, str] = Field(default_factory=dict)
    when_to_use: str | None = None
    """Trigger clause appended to the description in catalogs (Claude Code extension)."""
    disable_model_invocation: bool = Field(default=False, alias="disable-model-invocation")
    """When true, the skill is hidden from the model's catalog."""
    user_invocable: bool = Field(default=True, alias="user-invocable")
    """Whether a host should let a person invoke the skill directly."""
    extra_frontmatter: dict[str, Any] = Field(default_factory=dict, exclude=True)
    """Frontmatter keys this model has no field for, kept verbatim."""
    frontmatter: dict[str, Any] = Field(default_factory=dict, exclude=True)
    """The frontmatter exactly as parsed, when the skill was built from one."""
    server: str | None = Field(default=None, exclude=True)
    """Name of the MCP server providing this skill; None for skills found on a path."""
    agent_accessible: bool = Field(default=True, exclude=True)
    """Whether the agent's own file and shell tools can reach ``skill_path``."""
    instructions: str | None = Field(default=None, exclude=True)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        name = unicodedata.normalize("NFKC", v.strip())
        if not name:
            raise ValueError("Skill name must be non-empty")
        if name != name.lower():
            raise ValueError(f"Skill name {name!r} must be lowercase")
        if name.startswith("-") or name.endswith("-"):
            raise ValueError("Skill name cannot start or end with a hyphen")
        if "--" in name:
            raise ValueError("Skill name cannot contain consecutive hyphens")
        if not all(c.isalnum() or c == "-" for c in name):
            msg = (
                f"Skill name {name!r} contains invalid characters. "
                "Only letters, digits, and hyphens are allowed."
            )
            raise ValueError(msg)
        return name

    @model_validator(mode="before")
    @classmethod
    def _normalize_metadata(cls, data: Any) -> Any:
        if isinstance(data, dict) and "metadata" in data:
            meta = data["metadata"]
            if isinstance(meta, dict):
                data["metadata"] = {str(k): str(v) for k, v in meta.items()}
        return data

    @classmethod
    def from_frontmatter(
        cls,
        skill_path: UPath,
        frontmatter: Mapping[str, Any],
        *,
        instructions: str | None = None,
        server: str | None = None,
        agent_accessible: bool = True,
    ) -> Skill:
        """Build a Skill from parsed frontmatter.

        Keys without a field on this model are kept in ``extra_frontmatter``. A name
        that differs from the skill directory's name is logged, and the skill still
        loads (lenient validation, as the Agent Skills client guide recommends).

        Args:
            skill_path: Path to the skill directory.
            frontmatter: Parsed YAML frontmatter of the skill's SKILL.md.
            instructions: The SKILL.md body, when already read.
            server: Name of the MCP server providing the skill, if any.
            agent_accessible: Whether the agent's own tools can reach ``skill_path``.

        Returns:
            Validated Skill instance.

        Raises:
            ValidationError: If fields fail Pydantic validation.
        """
        known = {k: v for k, v in frontmatter.items() if k in cls.FRONTMATTER_FIELDS}
        extra = {k: v for k, v in frontmatter.items() if k not in cls.FRONTMATTER_FIELDS}
        skill = cls(
            skill_path=skill_path,
            instructions=instructions,
            extra_frontmatter=extra,
            frontmatter=dict(frontmatter),
            server=server,
            agent_accessible=agent_accessible,
            **known,
        )
        if skill_path.name and skill_path.name != skill.name:
            logger.warning(
                "Skill name does not match its directory",
                skill=skill.name,
                directory=skill_path.name,
            )
        return skill

    @classmethod
    def from_skill_dir(cls, skill_dir: UPath) -> Skill:
        """Parse a SKILL.md file from a directory and return a validated Skill.

        Args:
            skill_dir: Path to the skill directory containing SKILL.md.

        Returns:
            Validated Skill instance.

        Raises:
            FileNotFoundError: If SKILL.md is not found.
            ValueError: If frontmatter is missing or invalid YAML.
            ValidationError: If fields fail Pydantic validation.
        """
        skill_file = find_skill_md(skill_dir)
        if skill_file is None:
            raise FileNotFoundError(f"SKILL.md not found in {skill_dir}")

        return cls.from_skill_md(skill_dir, skill_file.read_text("utf-8"))

    @classmethod
    def from_skill_md(cls, skill_dir: UPath, content: str) -> Skill:
        """Build a Skill from SKILL.md content that has already been read.

        The instructions are taken from the content, so they are never read
        again from ``skill_dir``. Use this when the file comes from an async
        filesystem that cannot be read synchronously later.

        Args:
            skill_dir: Path to the skill directory containing SKILL.md.
            content: Raw content of the SKILL.md file.

        Returns:
            Validated Skill instance with instructions loaded.

        Raises:
            ValueError: If frontmatter is missing or invalid YAML.
            ValidationError: If fields fail Pydantic validation.
        """
        metadata, body = parse_frontmatter(content)
        return cls.from_frontmatter(skill_dir, metadata, instructions=body)

    @property
    def qualified_name(self) -> str:
        """Name the skills tools address this skill by: ``<server>/<name>`` for MCP skills."""
        return f"{self.server}/{self.name}" if self.server else self.name

    @property
    def catalog_description(self) -> str:
        """Description as listed in catalogs: the description, then ``when_to_use``."""
        return f"{self.description} {self.when_to_use}" if self.when_to_use else self.description

    @property
    def ignored_behavioral_fields(self) -> tuple[str, ...]:
        """Frontmatter fields such as ``hooks`` whose behavior AgentPool does not implement."""
        keys = set(self.frontmatter) | set(self.extra_frontmatter)
        return tuple(sorted(BEHAVIORAL_FRONTMATTER_FIELDS & keys))

    @property
    def display_directory(self) -> str:
        """The skill directory as the agent's own tools address it."""
        from aclientp.filesystem import ACPPath

        # Skills from the ACP client live on its filesystem; the session's file
        # tools address them by plain path, not by the ACP URL.
        if isinstance(self.skill_path, ACPPath):
            return self.skill_path.path
        return str(self.skill_path)

    def _filesystem(self) -> tuple[AsyncFileSystem, str]:
        from upathtools.async_ops import to_async_fs

        return to_async_fs(self.skill_path.fs), self.skill_path.path.rstrip("/")

    async def load_instructions(self) -> str:
        """Load the SKILL.md body, reading it through the skill's filesystem once.

        For skills from an MCP server, the frontmatter read must match the
        frontmatter the server advertised for the skill.

        Raises:
            SkillVerificationError: If an MCP skill's frontmatter differs from its entry.
        """
        if self.instructions is None:
            content = (await self.read_file(SKILL_FILE)).decode("utf-8")
            metadata, body = parse_frontmatter(content)
            if self.server is not None and _canonical(metadata) != _canonical(self.frontmatter):
                msg = f"SKILL.md of {self.qualified_name!r} does not match its advertised entry"
                raise SkillVerificationError(msg)
            self.instructions = body
        return self.instructions

    async def list_files(self) -> list[str]:
        """List every file bundled with the skill, relative to the skill directory."""
        fs, root = self._filesystem()
        from agentpool.utils.listing_filter import filter_listing

        found = await filter_listing(fs, root, await fs._find(root, withdirs=False))
        prefix = f"{fs._strip_protocol(root).rstrip('/')}/"
        files = [
            stripped[len(prefix) :]
            for path in found
            if (stripped := fs._strip_protocol(path)).startswith(prefix)
        ]
        return sorted(files)

    async def read_file(self, path: str) -> bytes:
        """Read one file bundled with the skill.

        Args:
            path: Path relative to the skill directory, such as ``references/guide.md``.

        Returns:
            The file's content.

        Raises:
            ValueError: If the path is absolute or leads outside the skill directory.
            FileNotFoundError: If the skill has no such file.
        """
        relative = normalize_skill_relative_path(path)
        fs, root = self._filesystem()
        target = f"{root}/{relative}"
        if self.skill_path.protocol in _LOCAL_PROTOCOLS:
            real_root = os.path.realpath(root)
            if not os.path.realpath(target).startswith(real_root + os.sep):
                raise ValueError(f"{path!r} is outside the skill directory")
        if await fs._isdir(target):
            raise FileNotFoundError(f"{path!r} is a directory")
        content = await fs._cat_file(target)
        if not isinstance(content, bytes):
            raise TypeError(f"Filesystem returned {type(content).__name__} for {path!r}")
        return content

    async def render(self) -> str:
        """Render the skill as the model receives it when the skill is loaded.

        The result is wrapped in ``<skill_content>``, ends with the bundled files
        (listed, not read), and names the skill directory only when the agent's own
        tools can reach it; otherwise it points to ``read_skill_file``.
        """
        body = await self.load_instructions()
        if self.agent_accessible:
            directory = self.display_directory
            body = _SKILL_DIR_PLACEHOLDER.sub(lambda _: directory, body)
        parts = [f"# {self.name}\n\n{self.description}"]
        meta_lines: list[str] = []
        if self.license:
            meta_lines.append(f"License: {self.license}")
        if self.compatibility:
            meta_lines.append(f"Compatibility: {self.compatibility}")
        if self.allowed_tools:
            meta_lines.append(f"Allowed tools: {self.allowed_tools}")
        if meta_lines:
            parts.append("\n".join(meta_lines))
        if body:
            parts.append(body)
        if self.agent_accessible:
            parts.append(
                f"Skill directory: {self.display_directory}\n"
                "Relative paths in this skill are relative to the skill directory."
            )
        else:
            parts.append(
                "Read files bundled with this skill with the read_skill_file tool "
                f"(skill_name={self.qualified_name!r}); paths are relative to the skill root."
            )
        resources = [f for f in await self.list_files() if f != SKILL_FILE]
        if resources:
            lines = ["<skill_resources>"]
            lines.extend(
                f"  <file>{html.escape(f)}</file>" for f in resources[:MAX_LISTED_RESOURCES]
            )
            if len(resources) > MAX_LISTED_RESOURCES:
                lines.append(f"  <!-- {len(resources) - MAX_LISTED_RESOURCES} more files -->")
            lines.append("</skill_resources>")
            parts.append("\n".join(lines))
        content = "\n\n".join(parts)
        name_attr = html.escape(self.qualified_name, quote=True)
        return f'<skill_content name="{name_attr}">\n{content}\n</skill_content>'


def normalize_skill_relative_path(path: str) -> str:
    """Normalize a path relative to a skill directory.

    Args:
        path: Path such as ``references/guide.md``.

    Returns:
        The normalized POSIX path.

    Raises:
        ValueError: If the path is empty, absolute, or leads outside the skill directory.
    """
    if not path or path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path):
        raise ValueError(f"Give a path relative to the skill directory, not {path!r}")
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if normalized in {".", ".."} or normalized.startswith("../"):
        raise ValueError(f"{path!r} is outside the skill directory")
    return normalized


def _canonical(frontmatter: Mapping[str, Any]) -> str:
    return json.dumps(frontmatter, sort_keys=True, default=str)


def find_skill_md(skill_dir: UPath) -> UPath | None:
    """Find the SKILL.md file in a skill directory.

    Args:
        skill_dir: Path to the skill directory.

    Returns:
        Path to the SKILL.md file, or None if not found.
    """
    path = skill_dir / SKILL_FILE
    return path if path.exists() else None


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from SKILL.md content.

    Args:
        content: Raw content of a SKILL.md file.

    Returns:
        Tuple of (metadata dict, markdown body).

    Raises:
        ValueError: If frontmatter is missing or invalid.
    """
    import yamling

    if not content.startswith("---"):
        raise ValueError("SKILL.md must start with YAML frontmatter (---)")

    parts = content.split("---", 2)
    if len(parts) < 3:  # noqa: PLR2004
        raise ValueError("SKILL.md frontmatter not properly closed with ---")
    try:
        metadata = yamling.load_yaml(parts[1])
    except yamling.YAMLError as e:
        raise ValueError(f"Invalid YAML in frontmatter: {e}") from e

    if not isinstance(metadata, dict):
        raise TypeError("SKILL.md frontmatter must be a YAML mapping")

    return metadata, parts[2].strip()


def to_prompt(skills: list[Skill]) -> str:
    """Generate the ``<available_skills>`` XML block for agent system prompts.

    This XML format is what Anthropic uses and recommends for Claude models.
    Skills with ``disable-model-invocation`` are left out. The location is only
    given for skills the agent's own tools can reach.

    Args:
        skills: List of skills to include.

    Returns:
        XML string with ``<available_skills>`` block.
    """
    visible = [s for s in skills if not s.disable_model_invocation]
    if not visible:
        return "<available_skills>\n</available_skills>"

    lines = ["<available_skills>"]
    for skill in visible:
        lines.append("<skill>")
        lines.append(f"<name>{html.escape(skill.qualified_name)}</name>")
        lines.append(f"<description>{html.escape(skill.catalog_description)}</description>")
        if skill.agent_accessible:
            lines.append(f"<location>{skill.display_directory}/{SKILL_FILE}</location>")
        lines.append("</skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)
