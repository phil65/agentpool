"""Config options of the Claude Code agent."""

from typing import TYPE_CHECKING, Any, cast

from agentpool.agents.modes import ConfigOption, ModelOption


if TYPE_CHECKING:
    from clawd_code_sdk.models import PermissionMode, ReasoningEffort

    from agentpool.agents.claude_code_agent.claude_code_agent import ClaudeCodeAgent

    type AnyClaudeCodeAgent = ClaudeCodeAgent[Any, Any]

    from agentpool.agents.modes import ModeInfo


class PermissionModeOption(ConfigOption["AnyClaudeCodeAgent"]):
    """Claude Code's permission mode."""

    id = "mode"
    name = "Mode"
    category = "mode"

    async def choices(self, agent: AnyClaudeCodeAgent) -> list[ModeInfo]:
        from agentpool.agents.claude_code_agent.static_info import MODES

        return MODES

    def current(self, agent: AnyClaudeCodeAgent) -> str:
        return agent._permission_mode or "default"

    async def _apply(self, agent: AnyClaudeCodeAgent, value: str) -> None:
        agent._remember_permission_mode(cast("PermissionMode", value))
        if agent._client:
            await agent.ensure_initialized()
            await agent._client.set_permission_mode(agent._permission_mode)


class ClaudeModelOption(ModelOption["AnyClaudeCodeAgent"]):
    """Model selection, limited to the models Claude Code offers."""

    strict = True

    async def _apply(self, agent: AnyClaudeCodeAgent, value: str) -> None:
        agent._model = value
        if agent._client:
            await agent.ensure_initialized()
            await agent._client.set_model(value)
            await agent._refresh_context_window()
        # An effort the new model lacks would keep running unseen: move to one it has.
        levels = agent._effort_levels()
        if agent._effort and levels and agent._effort not in levels:
            await agent._apply_effort(closest_effort(agent._effort, levels))


EFFORT_ORDER: tuple[ReasoningEffort, ...] = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT: ReasoningEffort = "high"


def closest_effort(effort: ReasoningEffort, levels: list[ReasoningEffort]) -> ReasoningEffort:
    """The highest of ``levels`` up to ``effort``, else the lowest of them."""
    rank = EFFORT_ORDER.index
    below = [level for level in levels if rank(level) <= rank(effort)]
    return max(below, key=rank) if below else min(levels, key=rank)


class EffortOption(ConfigOption["AnyClaudeCodeAgent"]):
    """Reasoning effort (depth of adaptive thinking), with the levels of the selected model."""

    id = "thought_level"
    name = "Reasoning Effort"
    category = "thought_level"

    async def choices(self, agent: AnyClaudeCodeAgent) -> list[ModeInfo]:
        from agentpool.agents.claude_code_agent.static_info import EFFORT_MODES

        if (levels := agent._effort_levels()) is None:
            return EFFORT_MODES
        return [mode for mode in EFFORT_MODES if mode.value in levels]

    def current(self, agent: AnyClaudeCodeAgent) -> str:
        levels = agent._effort_levels()
        effort = agent._effort or DEFAULT_EFFORT
        if levels is None or effort in levels:
            return effort
        return closest_effort(effort, levels) if levels else ""

    async def _apply(self, agent: AnyClaudeCodeAgent, value: str) -> None:
        await agent._apply_effort(cast("ReasoningEffort", value))


class FastModeOption(ConfigOption["AnyClaudeCodeAgent"]):
    """Fast mode, offered for the models that have it."""

    id = "fast"
    name = "Fast Mode"
    category = "other"

    async def choices(self, agent: AnyClaudeCodeAgent) -> list[ModeInfo]:
        from agentpool.agents.claude_code_agent.static_info import FAST_MODES

        model = agent._current_model_info()
        return FAST_MODES if model is not None and model.supports_fast_mode else []

    def current(self, agent: AnyClaudeCodeAgent) -> str:
        return "on" if agent._fast_mode else "off"

    async def _apply(self, agent: AnyClaudeCodeAgent, value: str) -> None:
        enabled = value == "on"
        agent._fast_mode = enabled
        if agent._client:
            await agent.ensure_initialized()
            await agent._apply_fast_mode(enabled=enabled)


CONFIG_OPTIONS: tuple[ConfigOption[AnyClaudeCodeAgent], ...] = (
    PermissionModeOption(),
    ClaudeModelOption(),
    EffortOption(),
    FastModeOption(),
)
"""Options of every Claude Code agent, in display order."""
