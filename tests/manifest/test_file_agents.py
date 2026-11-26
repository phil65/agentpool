"""Tests for Claude Code style file_agents parsing and loading."""

import tempfile
from typing import TYPE_CHECKING

import pytest

from agentpool import AgentsManifest
from agentpool.models.file_parsing import (
    CLAUDE_MODEL_ALIASES,
    PERMISSION_MODE_MAP,
    parse_agent_file,
    parse_opencode_format,
)


if TYPE_CHECKING:
    from pathlib import Path


def get_model_identifier(model) -> str | None:
    """Extract model identifier from various model config types."""
    if model is None:
        return None
    if isinstance(model, str):
        return model
    # Handle StringModelConfig and similar
    if hasattr(model, "identifier"):
        return model.identifier
    return str(model)


VALID_CLAUDE_AGENT = """\
---
name: code-reviewer
description: Expert code reviewer for quality and security
model: sonnet
permissionMode: default
---

You are a senior code reviewer ensuring high standards of code quality.

Focus on:
- Code readability
- Security issues
- Performance
"""

MINIMAL_CLAUDE_AGENT = """\
---
description: A minimal agent
model: openai:gpt-4o-mini
---

Just a simple system prompt.
"""

AGENT_WITH_AGENTPOOL_EXTENSIONS = """\
---
name: extended-agent
description: Agent with agentpool-specific fields
model: opus
retries: 3
avatar: https://example.com/avatar.png
---

Extended agent prompt.
"""

AGENT_WITH_UNKNOWN_PERMISSION = """\
---
description: Agent with unknown permission mode
model: openai:gpt-4o-mini
permissionMode: plan
---

Unknown permission mode test.
"""

INVALID_FRONTMATTER = """\
No frontmatter here, just content.
"""

INVALID_YAML = """\
---
name: [invalid: yaml: here
---

Content.
"""

OPENCODE_AGENT = """\
---
description: Reviews code for quality and best practices
mode: subagent
model: anthropic/claude-sonnet-4-20250514
temperature: 0.1
tools:
  write: false
  edit: false
  bash: false
permission:
  edit: deny
  bash:
    "git diff": allow
    "*": ask
---

You are in code review mode. Focus on:

- Code quality and best practices
- Potential bugs and edge cases
- Performance implications
"""

OPENCODE_AGENT_WITH_MAXSTEPS = """\
---
description: Fast reasoning with limited iterations
mode: subagent
model: anthropic/claude-sonnet-4-20250514
maxSteps: 5
---

Quick thinker agent.
"""


def test_parse_valid_claude_agent():
    """Test parsing a valid Claude Code style agent file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(VALID_CLAUDE_AGENT)
        f.flush()

        config = parse_agent_file(f.name)

        assert config.description == "Expert code reviewer for quality and security"
        assert get_model_identifier(config.model) == CLAUDE_MODEL_ALIASES["sonnet"]
        assert config.requires_tool_confirmation == PERMISSION_MODE_MAP["default"]
        assert config.system_prompt is not None
        assert "senior code reviewer" in str(config.system_prompt)
        assert "Code readability" in str(config.system_prompt)


def test_parse_minimal_agent():
    """Test parsing a minimal agent with only required fields."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(MINIMAL_CLAUDE_AGENT)
        f.flush()

        config = parse_agent_file(f.name)

        assert config.description == "A minimal agent"
        assert get_model_identifier(config.model) == "openai:gpt-4o-mini"
        assert config.system_prompt is not None
        assert "simple system prompt" in str(config.system_prompt)


def test_parse_agent_with_agentpool_extensions():
    """Test that agentpool-specific fields are passed through."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(AGENT_WITH_AGENTPOOL_EXTENSIONS)
        f.flush()

        config = parse_agent_file(f.name)

        assert config.description == "Agent with agentpool-specific fields"
        assert get_model_identifier(config.model) == CLAUDE_MODEL_ALIASES["opus"]
        assert config.retries == 3
        assert config.avatar == "https://example.com/avatar.png"


def test_parse_agent_unknown_permission_mode(caplog):
    """Test that unknown permission modes are logged and use default."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(AGENT_WITH_UNKNOWN_PERMISSION)
        f.flush()

        config = parse_agent_file(f.name)

        # Should use default (per_tool)
        assert config.requires_tool_confirmation == "per_tool"


def test_parse_missing_frontmatter():
    """Test error when frontmatter is missing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(INVALID_FRONTMATTER)
        f.flush()

        with pytest.raises(ValueError, match="No YAML frontmatter found"):
            parse_agent_file(f.name)


def test_parse_invalid_yaml():
    """Test error when YAML is invalid."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(INVALID_YAML)
        f.flush()

        with pytest.raises(ValueError, match="Invalid YAML frontmatter"):
            parse_agent_file(f.name)


def test_model_alias_mapping():
    """Test all Claude Code model aliases map correctly."""
    assert "sonnet" in CLAUDE_MODEL_ALIASES
    assert "opus" in CLAUDE_MODEL_ALIASES
    assert "haiku" in CLAUDE_MODEL_ALIASES

    for model in CLAUDE_MODEL_ALIASES.values():
        assert "anthropic:" in model


def test_permission_mode_mapping():
    """Test permission mode mappings."""
    assert PERMISSION_MODE_MAP["default"] == "per_tool"
    assert PERMISSION_MODE_MAP["acceptEdits"] == "never"
    assert PERMISSION_MODE_MAP["bypassPermissions"] == "never"


def test_manifest_with_file_agents(tmp_path: Path):
    """Test AgentsManifest with file_agents field."""
    # Create agent file
    agent_file = tmp_path / "test_agent.md"
    agent_file.write_text(VALID_CLAUDE_AGENT)

    manifest = AgentsManifest(
        file_agents={"code_reviewer": str(agent_file)},
    )

    # Check node_names includes file agents
    assert "code_reviewer" in manifest.node_names

    # Check nodes includes loaded config
    assert "code_reviewer" in manifest.nodes
    config = manifest.nodes["code_reviewer"]
    assert config.description == "Expert code reviewer for quality and security"


def test_manifest_file_agents_name_override(tmp_path: Path):
    """Test that manifest key overrides name from frontmatter."""
    agent_file = tmp_path / "agent.md"
    agent_file.write_text(VALID_CLAUDE_AGENT)

    manifest = AgentsManifest(
        file_agents={"my_custom_name": str(agent_file)},
    )

    # The name should be set from the dict key
    config = manifest._loaded_file_agents["my_custom_name"]
    assert config.name == "my_custom_name"


def test_manifest_file_agents_mixed(tmp_path: Path):
    """Test manifest with both inline and file agents."""
    from agentpool.models.agents import NativeAgentConfig

    agent_file = tmp_path / "file_agent.md"
    agent_file.write_text(MINIMAL_CLAUDE_AGENT)

    manifest = AgentsManifest(
        agents={
            "inline_agent": NativeAgentConfig(
                model="openai:gpt-4o-mini",
                description="Inline agent",
                system_prompt="You are inline",
            ),
        },
        file_agents={"file_agent": str(agent_file)},
    )

    assert "inline_agent" in manifest.node_names
    assert "file_agent" in manifest.node_names
    assert len(manifest.node_names) == 2


def test_manifest_invalid_file_agent(tmp_path: Path):
    """Test error handling for invalid file agent."""
    nonexistent = tmp_path / "nonexistent.md"

    manifest = AgentsManifest(
        file_agents={"bad_agent": str(nonexistent)},
    )

    with pytest.raises(ValueError, match="Failed to load file agent"):
        _ = manifest._loaded_file_agents


def test_empty_system_prompt():
    """Test agent with empty body after frontmatter."""
    content = """\
---
description: Agent with no body
model: haiku
---
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        f.flush()

        config = parse_agent_file(f.name)

        assert config.description == "Agent with no body"
        # Empty body means no system prompts
        assert config.system_prompt is None


def test_direct_model_name():
    """Test using a direct model name instead of alias."""
    content = """\
---
description: Agent with direct model
model: openai:gpt-4
---

Direct model test.
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        f.flush()

        config = parse_agent_file(f.name)

        assert get_model_identifier(config.model) == "openai:gpt-4"


def test_parse_opencode_agent():
    """Test parsing an OpenCode style agent file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(OPENCODE_AGENT)
        f.flush()

        config = parse_agent_file(f.name)

        assert config.description == "Reviews code for quality and best practices"
        assert get_model_identifier(config.model) == "anthropic/claude-sonnet-4-20250514"
        assert config.system_prompt is not None
        assert "code review mode" in str(config.system_prompt)
        assert "Code quality" in str(config.system_prompt)


def test_parse_opencode_with_maxsteps():
    """Test parsing OpenCode agent with maxSteps field."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(OPENCODE_AGENT_WITH_MAXSTEPS)
        f.flush()

        config = parse_agent_file(f.name)

        assert config.description == "Fast reasoning with limited iterations"
        assert "Quick thinker" in str(config.system_prompt)


def test_format_auto_detection():
    """Test that both formats are correctly auto-detected."""
    # Claude Code format detection
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(VALID_CLAUDE_AGENT)
        f.flush()
        config = parse_agent_file(f.name)
        # Should parse as Claude Code (with model alias mapping)
        assert get_model_identifier(config.model) == CLAUDE_MODEL_ALIASES["sonnet"]

    # OpenCode format detection
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(OPENCODE_AGENT)
        f.flush()
        config = parse_agent_file(f.name)
        # Should parse as OpenCode (with full model identifier)
        assert get_model_identifier(config.model) == "anthropic/claude-sonnet-4-20250514"


def test_opencode_edit_deny_is_not_auto_approve():
    """`permission.edit: deny` must block edit tools, never auto-approve everything (#45.3)."""
    kwargs = parse_opencode_format({"permission": {"edit": "deny"}}, "", "agent.md")
    assert kwargs.get("requires_tool_confirmation") != "never"


async def test_opencode_permission_rules_become_hooks():
    """Deny blocks edit tools, '*': ask (with patterns) asks for bash, other tools run."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(OPENCODE_AGENT)
        f.flush()
        config = parse_agent_file(f.name)
    assert config.requires_tool_confirmation == "per_tool"
    hooks = config.hooks.get_agent_hooks()

    async def decide(tool_name: str) -> str | None:
        result = await hooks.run_pre_tool_hooks(agent_name="a", tool_name=tool_name, tool_input={})
        return result.get("decision")

    assert await decide("edit") == "deny"
    assert await decide("write") == "deny"
    assert await decide("bash") == "ask"
    assert await decide("read") in (None, "allow")
