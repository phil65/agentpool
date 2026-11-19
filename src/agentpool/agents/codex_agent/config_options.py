"""Config options of the Codex agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from agentpool.agents.modes import ConfigOption, ModelOption


if TYPE_CHECKING:
    from codexed import Personality, SandboxMode

    from agentpool.agents.codex_agent.codex_agent import CodexAgent
    from agentpool.agents.modes import ModeInfo
    from agentpool.models.codex_agents import ApprovalPolicy

    type AnyCodexAgent = CodexAgent[Any, Any]


class ApprovalPolicyOption(ConfigOption["AnyCodexAgent"]):
    """When Codex asks before running tools."""

    id = "mode"
    name = "Tool Approval"
    category = "mode"

    async def choices(self, agent: AnyCodexAgent) -> list[ModeInfo]:
        from agentpool.agents.codex_agent.static_info import POLICY_MODES

        return POLICY_MODES

    def current(self, agent: AnyCodexAgent) -> str:
        return agent._approval_policy

    async def _apply(self, agent: AnyCodexAgent, value: str) -> None:
        agent._approval_policy = cast("ApprovalPolicy", value)


class ReasoningEffortOption(ConfigOption["AnyCodexAgent"]):
    """Reasoning effort of the model."""

    id = "thought_level"
    name = "Reasoning Effort"
    category = "thought_level"

    async def choices(self, agent: AnyCodexAgent) -> list[ModeInfo]:
        from agentpool.agents.codex_agent.static_info import EFFORT_MODES

        return EFFORT_MODES

    def current(self, agent: AnyCodexAgent) -> str:
        return agent._current_effort or "medium"

    async def _apply(self, agent: AnyCodexAgent, value: str) -> None:
        agent._current_effort = value


class SandboxOption(ConfigOption["AnyCodexAgent"]):
    """File system access of the sandbox."""

    id = "sandbox"
    name = "Sandbox Mode"
    category = "other"

    async def choices(self, agent: AnyCodexAgent) -> list[ModeInfo]:
        from agentpool.agents.codex_agent.static_info import SANDBOX_MODES

        return SANDBOX_MODES

    def current(self, agent: AnyCodexAgent) -> str:
        return agent._current_sandbox or "workspace-write"

    async def _apply(self, agent: AnyCodexAgent, value: str) -> None:
        agent._current_sandbox = cast("SandboxMode", value)


class PersonalityOption(ConfigOption["AnyCodexAgent"]):
    """Personality preset of the model."""

    id = "personality"
    name = "Personality"
    category = "other"

    async def choices(self, agent: AnyCodexAgent) -> list[ModeInfo]:
        from agentpool.agents.codex_agent.static_info import PERSONALITY_MODES

        return PERSONALITY_MODES

    def current(self, agent: AnyCodexAgent) -> str:
        return agent._current_personality or "none"

    async def _apply(self, agent: AnyCodexAgent, value: str) -> None:
        agent._current_personality = cast("Personality", value)


class CodexModelOption(ModelOption["AnyCodexAgent"]):
    """Model selection; Codex accepts models outside its list."""

    async def _apply(self, agent: AnyCodexAgent, value: str) -> None:
        agent._current_model = value


CONFIG_OPTIONS: tuple[ConfigOption[AnyCodexAgent], ...] = (
    ApprovalPolicyOption(),
    ReasoningEffortOption(),
    SandboxOption(),
    PersonalityOption(),
    CodexModelOption(),
)
"""Options of every Codex agent, in display order."""
