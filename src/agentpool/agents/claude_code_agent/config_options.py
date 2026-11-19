"""Config options of the Claude Code agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from agentpool.agents.modes import ConfigOption, ModelOption


if TYPE_CHECKING:
    from clawd_code_sdk.models import PermissionMode, ReasoningEffort

    from agentpool.agents.claude_code_agent.claude_code_agent import (
        ClaudeCodeAgent,
        ThinkingMode,
    )

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
        agent._permission_mode = cast("PermissionMode", value)
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


class ThinkingLevelOption(ConfigOption["AnyClaudeCodeAgent"]):
    """Budget of thinking tokens."""

    id = "thought_level"
    name = "Thinking Level"
    category = "thought_level"

    async def choices(self, agent: AnyClaudeCodeAgent) -> list[ModeInfo]:
        from agentpool.agents.claude_code_agent.static_info import THINKING_MODES

        return THINKING_MODES

    def current(self, agent: AnyClaudeCodeAgent) -> str:
        return agent._thinking_mode

    async def _apply(self, agent: AnyClaudeCodeAgent, value: str) -> None:
        from agentpool.agents.claude_code_agent.claude_code_agent import THINKING_MODE_TOKENS

        agent._thinking_mode = cast("ThinkingMode", value)
        if agent._client:
            await agent.ensure_initialized()
            await agent._client.set_max_thinking_tokens(THINKING_MODE_TOKENS[agent._thinking_mode])


class EffortOption(ConfigOption["AnyClaudeCodeAgent"]):
    """Reasoning effort; a CLI startup flag, so changing it reconnects the session."""

    id = "effort"
    name = "Reasoning Effort"
    category = "other"

    async def choices(self, agent: AnyClaudeCodeAgent) -> list[ModeInfo]:
        from agentpool.agents.claude_code_agent.static_info import EFFORT_MODES

        return EFFORT_MODES

    def current(self, agent: AnyClaudeCodeAgent) -> str:
        return agent._effort or "high"

    async def _apply(self, agent: AnyClaudeCodeAgent, value: str) -> None:
        agent._effort = cast("ReasoningEffort", value)
        if agent._client:
            await agent.reconnect(resume_session=True)


CONFIG_OPTIONS: tuple[ConfigOption[AnyClaudeCodeAgent], ...] = (
    PermissionModeOption(),
    ClaudeModelOption(),
    ThinkingLevelOption(),
    EffortOption(),
)
"""Options of every Claude Code agent, in display order."""
