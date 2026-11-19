"""Static model information."""

from __future__ import annotations

from tokonomics.model_discovery.model_info import ModelInfo, ModelPricing

from agentpool.agents.modes import ModeInfo


# Static Claude Code models - these are the simple IDs the SDK accepts
# Use id_override to ensure pydantic_ai_id returns simple names like "opus"

OPUS = ModelInfo(
    id="claude-opus-4-5",
    name="Claude Opus",
    provider="anthropic",
    description="Claude Opus - most capable model",
    context_window=200000,
    max_output_tokens=32000,
    input_modalities={"text", "image"},
    output_modalities={"text"},
    pricing=ModelPricing(
        prompt=0.000005,  # $5 per 1M tokens
        completion=0.000025,  # $25 per 1M tokens
    ),
    id_override="opus",  # Claude Code SDK uses simple names
)
SONNET = ModelInfo(
    id="claude-sonnet-4-5",
    name="Claude Sonnet",
    provider="anthropic",
    description="Claude Sonnet - balanced performance and speed",
    context_window=200000,
    max_output_tokens=16000,
    input_modalities={"text", "image"},
    output_modalities={"text"},
    pricing=ModelPricing(
        prompt=0.000003,  # $3 per 1M tokens
        completion=0.000015,  # $15 per 1M tokens
    ),
    id_override="sonnet",  # Claude Code SDK uses simple names
)
HAIKU = ModelInfo(
    id="claude-haiku-4-5",
    name="Claude Haiku",
    provider="anthropic",
    description="Claude Haiku - fast and cost-effective",
    context_window=200000,
    max_output_tokens=8000,
    input_modalities={"text", "image"},
    output_modalities={"text"},
    pricing=ModelPricing(
        prompt=0.000001,  # $1.00 per 1M tokens
        completion=0.000005,  # $5 per 1M tokens
    ),
    id_override="haiku",  # Claude Code SDK uses simple names
)

MODELS = [OPUS, SONNET, HAIKU]


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
        category_id="effort",
    ),
    ModeInfo(
        value="medium",
        name="Medium",
        description="Balanced reasoning effort",
        category_id="effort",
    ),
    ModeInfo(
        value="high",
        name="High",
        description="Deeper reasoning for complex tasks",
        category_id="effort",
    ),
    ModeInfo(
        value="xhigh",
        name="Extra High",
        description="Very deep reasoning for hard problems",
        category_id="effort",
    ),
    ModeInfo(
        value="max",
        name="Max",
        description="Maximum reasoning depth",
        category_id="effort",
    ),
]

THINKING_MODES = [
    ModeInfo(
        value="off",
        name="Off",
        description="No extended thinking",
        category_id="thought_level",
    ),
    ModeInfo(
        value="4k",
        name="4K tokens",
        description="Light reasoning (4,096 tokens)",
        category_id="thought_level",
    ),
    ModeInfo(
        value="8k",
        name="8K tokens",
        description="Moderate reasoning (8,192 tokens)",
        category_id="thought_level",
    ),
    ModeInfo(
        value="16k",
        name="16K tokens",
        description="Deep reasoning (16,384 tokens)",
        category_id="thought_level",
    ),
    ModeInfo(
        value="32k",
        name="32K tokens",
        description="Maximum reasoning (32,768 tokens)",
        category_id="thought_level",
    ),
]
