"""Config options of the Google Antigravity agent."""

from typing import TYPE_CHECKING, Any, cast

from agentpool.agents.modes import ConfigOption, ModelOption


if TYPE_CHECKING:
    from agentpool.agents.antigravity_agent.antigravity_agent import AntigravityAgent
    from agentpool.agents.modes import ModeInfo
    from agentpool.models.antigravity_agents import (
        AntigravityBehavior,
        AntigravityPolicyMode,
        AntigravityThinkingLevel,
    )

    type AnyAntigravityAgent = AntigravityAgent[Any, Any]


class PolicyModeOption(ConfigOption["AnyAntigravityAgent"]):
    """Tool execution approval policy preset."""

    id = "mode"
    name = "Tool Policy"
    category = "mode"

    async def choices(self, agent: AnyAntigravityAgent) -> list[ModeInfo]:
        from agentpool.agents.antigravity_agent.static_info import POLICY_MODES

        return POLICY_MODES

    def current(self, agent: AnyAntigravityAgent) -> str:
        return agent._policy_mode

    async def _apply(self, agent: AnyAntigravityAgent, value: str) -> None:
        agent._policy_mode = cast("AntigravityPolicyMode", value)
        agent._mark_needs_restart()


class ThinkingLevelOption(ConfigOption["AnyAntigravityAgent"]):
    """Extended thinking depth for Gemini models."""

    id = "thought_level"
    name = "Thinking Level"
    category = "thought_level"

    async def choices(self, agent: AnyAntigravityAgent) -> list[ModeInfo]:
        from agentpool.agents.antigravity_agent.static_info import THINKING_LEVELS

        return THINKING_LEVELS

    def current(self, agent: AnyAntigravityAgent) -> str:
        return agent._thinking_level or "medium"

    async def _apply(self, agent: AnyAntigravityAgent, value: str) -> None:
        agent._thinking_level = cast("AntigravityThinkingLevel", value)
        agent._mark_needs_restart()


class BehaviorOption(ConfigOption["AnyAntigravityAgent"]):
    """Operational execution behavior of the harness."""

    id = "behavior"
    name = "Behavior"
    category = "other"

    async def choices(self, agent: AnyAntigravityAgent) -> list[ModeInfo]:
        from agentpool.agents.antigravity_agent.static_info import BEHAVIOR_MODES

        return BEHAVIOR_MODES

    def current(self, agent: AnyAntigravityAgent) -> str:
        return agent._behavior

    async def _apply(self, agent: AnyAntigravityAgent, value: str) -> None:
        agent._behavior = cast("AntigravityBehavior", value)
        agent._mark_needs_restart()


class AntigravityModelOption(ModelOption["AnyAntigravityAgent"]):
    """Model selection; accepts models outside the static catalog too."""

    async def _apply(self, agent: AnyAntigravityAgent, value: str) -> None:
        agent._model = value
        agent._mark_needs_restart()


CONFIG_OPTIONS: tuple[ConfigOption[AnyAntigravityAgent], ...] = (
    PolicyModeOption(),
    ThinkingLevelOption(),
    BehaviorOption(),
    AntigravityModelOption(),
)
"""Options of every Antigravity agent, in display order."""
