"""Package resources for AgentPool configuration."""

import importlib.resources
from functools import cache
from typing import Final, Literal

_RESOURCES = importlib.resources.files("agentpool.text_templates")

DEFAULT_SYSTEM_PROMPT: Final[str] = str(_RESOURCES / "system_prompt.jinja")
"""Path to the default system prompt template."""

FormatStyle = Literal["simple", "detailed", "markdown"]


@cache
def get_system_prompt() -> str:
    """Get the default system prompt template content."""
    return (_RESOURCES / "system_prompt.jinja").read_text()


@cache
def get_tool_call_template(style: FormatStyle) -> str:
    """Get tool call template by style."""
    files = {
        "simple": "tool_call_simple.jinja",
        "detailed": "tool_call_default.jinja",
        "markdown": "tool_call_markdown.jinja",
    }
    return (_RESOURCES / files[style]).read_text()


__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "FormatStyle",
    "get_system_prompt",
    "get_tool_call_template",
]
