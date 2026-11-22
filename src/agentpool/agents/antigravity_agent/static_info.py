"""Static model catalog and mode definitions for the Antigravity agent."""

from __future__ import annotations

from tokonomics.model_discovery.model_info import Modality, ModelInfo

from agentpool.agents.modes import ModeInfo


DEFAULT_MODEL = "gemini-4-hydrogen"
"""Default text model used by the Google Antigravity SDK."""

_MULTIMODAL_INPUTS: frozenset[Modality] = frozenset({"text", "image", "audio", "video", "file"})


def _gemini_model(model_id: str, name: str, description: str) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        name=name,
        provider="google",
        description=description,
        input_modalities=set(_MULTIMODAL_INPUTS),
        output_modalities={"text"},
        id_override=model_id,
    )


MODELS: list[ModelInfo] = [
    _gemini_model(
        "gemini-4-hydrogen",
        "Gemini 4 Hydrogen (default)",
        "Default Antigravity SDK model for agentic coding and reasoning",
    ),
    _gemini_model(
        "gemini-3.1-pro-preview",
        "Gemini 3.1 Pro Preview",
        "High-capability Gemini reasoning model",
    ),
    _gemini_model(
        "gemini-3-flash-preview",
        "Gemini 3 Flash Preview",
        "Fast, cost-effective Gemini model for agentic workflows",
    ),
    _gemini_model(
        "gemini-2.5-pro",
        "Gemini 2.5 Pro",
        "Capable multimodal reasoning model",
    ),
    _gemini_model(
        "gemini-2.5-flash",
        "Gemini 2.5 Flash",
        "Fast multimodal model",
    ),
]
"""Curated Gemini models supported by the Antigravity SDK."""


POLICY_MODES: list[ModeInfo] = [
    ModeInfo(
        value="allow_all",
        name="Allow All",
        description="Approve all tool calls without confirmation",
        category_id="mode",
    ),
    ModeInfo(
        value="confirm_run_command",
        name="Confirm Commands",
        description="Allow file/other tools, ask confirmation before running shell commands",
        category_id="mode",
    ),
    ModeInfo(
        value="safe_defaults",
        name="Safe Defaults",
        description="Allow read-only tools and ask confirmation for writes, commands, and MCP",
        category_id="mode",
    ),
    ModeInfo(
        value="auto",
        name="Auto Safety",
        description="Assess tool calls with Antigravity's safety evaluator, asking when flagged",
        category_id="mode",
    ),
    ModeInfo(
        value="deny_all",
        name="Deny All",
        description="Deny all tool calls",
        category_id="mode",
    ),
]


THINKING_LEVELS: list[ModeInfo] = [
    ModeInfo(
        value="minimal",
        name="Minimal",
        description="Minimal reasoning overhead for quickest responses",
        category_id="thought_level",
    ),
    ModeInfo(
        value="low",
        name="Low",
        description="Light reasoning for straightforward tasks",
        category_id="thought_level",
    ),
    ModeInfo(
        value="medium",
        name="Medium",
        description="Balanced reasoning depth",
        category_id="thought_level",
    ),
    ModeInfo(
        value="high",
        name="High",
        description="Deep reasoning for complex coding and analysis",
        category_id="thought_level",
    ),
    ModeInfo(
        value="xhigh",
        name="Extra High",
        description="Maximum reasoning depth for the hardest tasks",
        category_id="thought_level",
    ),
]


BEHAVIOR_MODES: list[ModeInfo] = [
    ModeInfo(
        value="autonomous",
        name="Autonomous",
        description="Solve tasks end-to-end without stopping for human input",
        category_id="behavior",
    ),
    ModeInfo(
        value="interactive",
        name="Interactive",
        description="Work collaboratively, asking clarifying questions when needed",
        category_id="behavior",
    ),
    ModeInfo(
        value="minimal",
        name="Minimal",
        description="Streamlined system prompt for small-context or on-device models",
        category_id="behavior",
    ),
]
