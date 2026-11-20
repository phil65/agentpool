"""Static info of the Claude Code agent: model aliases and option choices."""

from __future__ import annotations

from tokonomics.model_discovery.model_info import ModelInfo

from agentpool.agents.claude_code_agent.converters import DEFAULT_CONTEXT_WINDOW
from agentpool.agents.modes import ModeInfo


def _alias(alias: str, name: str, description: str) -> ModelInfo:
    return ModelInfo(
        id=alias,
        name=name,
        provider="anthropic",
        description=description,
        context_window=DEFAULT_CONTEXT_WINDOW,
        input_modalities={"text", "image"},
        id_override=alias,
    )


MODELS = [
    _alias("default", "Default (recommended)", "The model Claude Code recommends"),
    _alias("opus", "Opus", "Most capable for complex work"),
    _alias("sonnet", "Sonnet", "Balanced performance and speed"),
    _alias("haiku", "Haiku", "Fastest for quick answers"),
]
"""The CLI's model aliases, offered until it is connected and lists the account's models."""


MODES = [
    ModeInfo(
        value="default",
        name="Default",
        description="Require confirmation for tool usage",
        category_id="mode",
    ),
    ModeInfo(
        value="acceptEdits",
        name="Accept Edits",
        description="Auto-approve file edits without confirmation",
        category_id="mode",
    ),
    ModeInfo(
        value="plan",
        name="Plan",
        description="Planning mode - no tool execution",
        category_id="mode",
    ),
    ModeInfo(
        value="bypassPermissions",
        name="Bypass Permissions",
        description="Skip all permission checks (use with caution)",
        category_id="mode",
    ),
    # ModeInfo(
    #     id="delegate",
    #     name="Delegate",
    #     description="Delegate mode, restricts to only Teammate and Task tools",
    #     category_id="mode",
    # ),
    # ModeInfo(
    #     id="dontAsk",
    #     name="Do not ask",
    #     description="Don't prompt for permissions, deny if not pre-approved",
    #     category_id="mode",
    # ),
]

EFFORT_MODES = [
    ModeInfo(
        value="low",
        name="Low",
        description="Faster, cheaper responses for straightforward tasks",
        category_id="thought_level",
    ),
    ModeInfo(
        value="medium",
        name="Medium",
        description="Balanced reasoning effort",
        category_id="thought_level",
    ),
    ModeInfo(
        value="high",
        name="High",
        description="Deeper reasoning for complex tasks",
        category_id="thought_level",
    ),
    ModeInfo(
        value="xhigh",
        name="Extra High",
        description="Very deep reasoning for hard problems",
        category_id="thought_level",
    ),
    ModeInfo(
        value="max",
        name="Max",
        description="Maximum reasoning depth",
        category_id="thought_level",
    ),
]

FAST_MODES = [
    ModeInfo(value="off", name="Off", description="Standard speed", category_id="fast"),
    ModeInfo(
        value="on",
        name="On",
        description="Faster responses at a higher price",
        category_id="fast",
    ),
]
