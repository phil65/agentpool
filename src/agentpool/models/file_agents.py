"""Configuration for file-based agent definitions.

Supports loading agents from markdown files with YAML frontmatter in various formats:
- Claude Code: https://code.claude.com/docs/en/sub-agents.md
- OpenCode: https://github.com/sst/opencode
- AgentPool (native): Full NativeAgentConfig fields in frontmatter
"""

from typing import Annotated, Literal

from pydantic import Field
from schemez import Schema


class ClaudeFileAgentConfig(Schema):
    """Configuration for a Claude Code format agent file.

    Claude Code agents use markdown files with YAML frontmatter containing
    fields like `model`, `tools`, `allowed-tools`, etc.

    Example:
        ```yaml
        file_agents:
          reviewer:
            type: claude
            path: .claude/agents/reviewer.md
        ```
    """

    type: Literal["claude"] = Field(
        default="claude",
        description="Discriminator for Claude Code format",
    )

    path: str = Field(
        ...,
        description="Path to the agent markdown file",
        examples=[".claude/agents/reviewer.md"],
    )


class OpenCodeFileAgentConfig(Schema):
    """Configuration for an OpenCode format agent file.

    OpenCode agents use markdown files with YAML frontmatter containing
    fields like `model`, `tools`, `description`, etc.

    Example:
        ```yaml
        file_agents:
          debugger:
            type: opencode
            path: ./agents/debugger.md
        ```
    """

    type: Literal["opencode"] = Field(
        default="opencode",
        description="Discriminator for OpenCode format",
    )

    path: str = Field(
        ...,
        description="Path to the agent markdown file",
        examples=["./agents/debugger.md"],
    )


class NativeFileAgentConfig(Schema):
    """Configuration for a native format agent file.

    Native agents use markdown files with YAML frontmatter containing
    full NativeAgentConfig fields (model, toolsets, knowledge, etc.).

    Example:
        ```yaml
        file_agents:
          assistant:
            type: native
            path: ./agents/assistant.md
        ```
    """

    type: Literal["native"] = Field(
        default="native",
        description="Discriminator for native format",
    )

    path: str = Field(
        ...,
        description="Path to the agent markdown file",
        examples=["./agents/assistant.md"],
    )


# Discriminated union of all file agent config types
FileAgentConfig = ClaudeFileAgentConfig | OpenCodeFileAgentConfig | NativeFileAgentConfig

# All supported file agent config types for documentation
FileAgentConfigTypes = ClaudeFileAgentConfig | OpenCodeFileAgentConfig | NativeFileAgentConfig

# Type alias for manifest usage: either a simple path string (auto-detect) or explicit config
FileAgentReference = Annotated[
    str | FileAgentConfig,
    Field(
        description="Agent file reference - either a path string (auto-detect format) "
        "or explicit config with type discriminator",
        examples=[
            ".claude/agents/reviewer.md",
            {"type": "claude", "path": "./agents/reviewer.md"},
            {"type": "opencode", "path": "./agents/debugger.md"},
        ],
    ),
]
