"""Parsing logic for file-based agent definitions.

Supports loading agents from markdown files with YAML frontmatter in various formats:
- Claude Code: https://code.claude.com/docs/en/sub-agents.md
- OpenCode: https://github.com/sst/opencode
- AgentPool (native): Full NativeAgentConfig fields in frontmatter
"""

import re
from typing import TYPE_CHECKING, Any, Literal, assert_never

from upathtools import to_upath

from agentpool.log import get_logger


if TYPE_CHECKING:
    from agentpool.models.agents import NativeAgentConfig
    from agentpool.models.file_agents import FileAgentConfig


logger = get_logger(__name__)
AgentFileFormat = Literal["claude", "opencode", "native"]

# Claude Code model alias mapping
CLAUDE_MODEL_ALIASES: dict[str, str] = {
    "sonnet": "anthropic:claude-sonnet-4-20250514",
    "opus": "anthropic:claude-opus-4-20250514",
    "haiku": "anthropic:claude-haiku-3-5-20241022",
}

# Claude Code permissionMode to ToolConfirmationMode mapping
PERMISSION_MODE_MAP: dict[str, Literal["always", "never", "per_tool"]] = {
    "default": "per_tool",
    "acceptEdits": "never",
    "bypassPermissions": "never",
    # 'plan' and 'ignore' don't map well, default to per_tool
}

# Fields that pass through directly to NativeAgentConfig
PASSTHROUGH_FIELDS = {
    "toolsets",
    "session",
    "output_type",
    "retries",
    "output_retries",
    "end_strategy",
    "avatar",
    "config_file_path",
    "knowledge",
    "workers",
    "debug",
    "usage_limits",
    "tool_mode",
    "display_name",
    "triggers",
}


def extract_frontmatter(content: str, file_path: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter and body from markdown content.

    Args:
        content: Full markdown file content
        file_path: Path for error messages

    Returns:
        Tuple of (metadata dict, system prompt body)

    Raises:
        ValueError: If frontmatter is missing or invalid
    """
    import yamling

    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", content, re.DOTALL)
    if not frontmatter_match:
        raise ValueError(f"No YAML frontmatter found in {file_path}")

    try:
        metadata = yamling.load_yaml(frontmatter_match.group(1))
    except yamling.YAMLError as e:
        raise ValueError(f"Invalid YAML frontmatter in {file_path}: {e}") from e

    if not isinstance(metadata, dict):
        raise ValueError(f"YAML frontmatter must be a dictionary in {file_path}")  # noqa: TRY004

    system_prompt = content[frontmatter_match.end() :].strip()
    return metadata, system_prompt


def detect_format(metadata: dict[str, Any]) -> AgentFileFormat:
    """Detect the file format based on frontmatter content.

    Args:
        metadata: Parsed YAML frontmatter

    Returns:
        Detected format: "claude", "opencode", or "agentpool"
    """
    # OpenCode indicators
    is_opencode = (
        any(key in metadata for key in ["mode", "temperature", "maxSteps", "disable"])
        or ("tools" in metadata and isinstance(metadata["tools"], dict))
        or ("permission" in metadata and isinstance(metadata["permission"], dict))
    )
    if is_opencode:
        return "opencode"

    # Native format indicators (agentpool specific fields)
    native_fields = {"toolsets", "session", "knowledge", "workers", "triggers"}
    if any(field in metadata for field in native_fields):
        return "native"

    # Default to Claude Code format
    return "claude"


def parse_claude_format(
    metadata: dict[str, Any],
    system_prompt: str,
    file_path: str,
    *,
    skills_registry: Any | None = None,
) -> dict[str, Any]:
    """Parse Claude Code format frontmatter.

    Args:
        metadata: Parsed YAML frontmatter
        system_prompt: Markdown body content
        file_path: Path for logging
        skills_registry: Optional skills registry for loading skills

    Returns:
        Dict of NativeAgentConfig kwargs
    """
    config_kwargs: dict[str, Any] = {}

    # Description
    if description := metadata.get("description"):
        config_kwargs["description"] = description

    # Model handling
    if model := metadata.get("model"):
        if model == "inherit":
            pass  # Leave as None, will use default
        elif model in CLAUDE_MODEL_ALIASES:
            config_kwargs["model"] = CLAUDE_MODEL_ALIASES[model]
        else:
            config_kwargs["model"] = model

    # Permission mode mapping
    if permission_mode := metadata.get("permissionMode"):
        if mapped := PERMISSION_MODE_MAP.get(permission_mode):
            config_kwargs["requires_tool_confirmation"] = mapped
        else:
            logger.warning(
                "Unknown permissionMode, using default",
                permission_mode=permission_mode,
                file_path=file_path,
            )

    # Tools string format (comma-separated) - not yet supported
    if (tools := metadata.get("tools")) and isinstance(tools, str):
        logger.debug("Claude Code tools string not yet supported", tools=tools, file_path=file_path)

    # Skills handling
    if (skills_str := metadata.get("skills")) and skills_registry is not None:
        skill_names = [s.strip() for s in skills_str.split(",")]
        for skill_name in skill_names:
            if skill_name not in skills_registry:
                logger.warning(
                    "Skill not found in registry, ignoring",
                    skill_name=skill_name,
                    file_path=file_path,
                )

    # System prompt from markdown body
    if system_prompt:
        config_kwargs["system_prompt"] = system_prompt

    # Pass through agentpool specific fields
    for field in PASSTHROUGH_FIELDS:
        if field in metadata:
            config_kwargs[field] = metadata[field]

    return config_kwargs


# OpenCode permission keys -> regex of the AgentPool tool names they cover
OPENCODE_PERMISSION_TOOLS: dict[str, str] = {
    "edit": (
        "^(write|edit|agentic_edit|edit_batch|multi_edit|regex_replace_lines"
        "|apply_patch|patch|delete_path)$"
    ),
    "bash": "^(bash|sandbox_bash|execute_command|execute_code)$",
    "webfetch": "^(webfetch|web_fetch|fetch|download_file)$",
}


def _opencode_permission_hooks(permission: Any, file_path: str) -> list[dict[str, Any]]:
    """Map an OpenCode ``permission`` block onto pre_tool_use hook configs.

    ``deny`` blocks the covered tools, ``ask`` asks the user before every call
    (whatever the agent's confirmation mode), ``allow`` keeps the default. Command
    patterns (``bash: {"git diff": allow, "*": ask}``) are not supported: the
    strictest of their decisions applies to the whole tool (fail closed).
    """
    if not isinstance(permission, dict):
        logger.warning(
            "Unsupported OpenCode permission", permission=permission, file_path=file_path
        )
        return []
    strictness = {"allow": 0, "ask": 1, "deny": 2}
    hooks: list[dict[str, Any]] = []
    for key, value in permission.items():
        matcher = OPENCODE_PERMISSION_TOOLS.get(key)
        decisions = list(value.values()) if isinstance(value, dict) else [value]
        if matcher is None or not decisions or any(d not in strictness for d in decisions):
            logger.warning(
                "Unsupported OpenCode permission rule", key=key, value=value, file_path=file_path
            )
            continue
        decision = max(decisions, key=strictness.__getitem__)
        if isinstance(value, dict) and len(value) > 1:
            logger.warning(
                "OpenCode permission patterns not supported, applying the strictest rule",
                key=key,
                decision=decision,
                file_path=file_path,
            )
        if decision == "allow":
            continue
        hooks.append({
            "type": "callable",
            "import_path": "agentpool.hooks.permission.fixed_decision",
            "matcher": matcher,
            "arguments": {"decision": decision, "reason": f"OpenCode permission {key}: {decision}"},
        })
    return hooks


def parse_opencode_format(
    metadata: dict[str, Any],
    system_prompt: str,
    file_path: str,
) -> dict[str, Any]:
    """Parse OpenCode format frontmatter.

    Args:
        metadata: Parsed YAML frontmatter
        system_prompt: Markdown body content
        file_path: Path for logging

    Returns:
        Dict of NativeAgentConfig kwargs
    """
    config_kwargs: dict[str, Any] = {}

    # Description
    if description := metadata.get("description"):
        config_kwargs["description"] = description

    # Model handling
    if model := metadata.get("model"):
        if model == "inherit":
            pass
        elif model in CLAUDE_MODEL_ALIASES:
            config_kwargs["model"] = CLAUDE_MODEL_ALIASES[model]
        else:
            config_kwargs["model"] = model

    # Temperature (logged, not directly supported)
    if temperature := metadata.get("temperature"):
        logger.debug(
            "OpenCode temperature not directly supported",
            temperature=temperature,
            file_path=file_path,
        )

    # MaxSteps (logged, not directly supported)
    if max_steps := metadata.get("maxSteps"):
        logger.debug(
            "OpenCode maxSteps not directly supported", max_steps=max_steps, file_path=file_path
        )

    # Disable (logged, not directly supported)
    if disable := metadata.get("disable"):
        logger.debug(
            "OpenCode disable not directly supported", disable=disable, file_path=file_path
        )

    # Mode (informational only)
    if mode := metadata.get("mode"):
        logger.debug("OpenCode mode informational only", mode=mode, file_path=file_path)

    # Permission handling (granular per-tool): "deny" / "ask" become pre_tool_use hooks
    # for the matching tools, so a denied tool is blocked and an "ask" one always asks.
    permission = metadata.get("permission")
    if permission and (hooks := _opencode_permission_hooks(permission, file_path)):
        config_kwargs["hooks"] = {"pre_tool_use": hooks}

    # Tools dict format (not yet supported)
    if (tools := metadata.get("tools")) and isinstance(tools, dict):
        logger.debug("OpenCode tools dict not yet supported", tools=tools, file_path=file_path)

    # System prompt from markdown body
    if system_prompt:
        config_kwargs["system_prompt"] = system_prompt

    # Pass through agentpool specific fields
    for field in PASSTHROUGH_FIELDS:
        if field in metadata:
            config_kwargs[field] = metadata[field]

    return config_kwargs


def parse_native_format(metadata: dict[str, Any], system_prompt: str) -> dict[str, Any]:
    """Parse native format frontmatter.

    This format allows full NativeAgentConfig fields in the frontmatter,
    with the markdown body used as system prompt.

    Args:
        metadata: Parsed YAML frontmatter
        system_prompt: Markdown body content

    Returns:
        Dict of NativeAgentConfig kwargs
    """
    # Start with all metadata (it's already in NativeAgentConfig format)
    config_kwargs = dict(metadata)

    # Add system prompt from body if present and not already defined
    if system_prompt and "system_prompt" not in config_kwargs:
        config_kwargs["system_prompt"] = system_prompt

    return config_kwargs


def parse_agent_file(
    file_path: str,
    *,
    file_format: AgentFileFormat | Literal["auto"] = "auto",
    skills_registry: Any | None = None,
) -> NativeAgentConfig:
    """Parse agent markdown file to NativeAgentConfig.

    Supports Claude Code, OpenCode, and native formats with auto-detection.
    Also supports local and remote paths via UPath.

    Args:
        file_path: Path to .md file with YAML frontmatter (local or remote)
        file_format: File format to use ("auto" for detection, or explicit format)
        skills_registry: Optional skills registry for loading skills

    Returns:
        Parsed NativeAgentConfig

    Raises:
        ValueError: If file cannot be parsed
    """
    from agentpool.models.agents import NativeAgentConfig

    path = to_upath(file_path)
    content = path.read_text("utf-8")

    # Extract frontmatter and body
    metadata, system_prompt = extract_frontmatter(content, file_path)

    # Detect or use specified format
    detected_format = detect_format(metadata) if file_format == "auto" else file_format
    match detected_format:
        case "claude":
            config_kwargs = parse_claude_format(
                metadata, system_prompt, file_path, skills_registry=skills_registry
            )
        case "opencode":
            config_kwargs = parse_opencode_format(metadata, system_prompt, file_path)
        case "native":
            config_kwargs = parse_native_format(metadata, system_prompt)
        case _ as unreachable:
            assert_never(unreachable)

    return NativeAgentConfig(**config_kwargs)


def parse_file_agent_reference(
    reference: str | FileAgentConfig,
    *,
    skills_registry: Any | None = None,
) -> NativeAgentConfig:
    """Parse a file agent reference (path string or config) to NativeAgentConfig.

    Args:
        reference: Either a path string (auto-detect format) or FileAgentConfig
            with explicit type discriminator
        skills_registry: Optional skills registry for loading skills

    Returns:
        Parsed NativeAgentConfig
    """
    if isinstance(reference, str):
        # Simple path string: auto-detect format
        return parse_agent_file(reference, skills_registry=skills_registry)

    # Explicit config: use type as format
    return parse_agent_file(
        reference.path,
        file_format=reference.type,
        skills_registry=skills_registry,
    )
