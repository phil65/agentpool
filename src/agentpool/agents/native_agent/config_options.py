"""Config options of the native agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from agentpool.agents.modes import ConfigOption, ModeInfo, ModelOption


if TYPE_CHECKING:
    from agentpool.agents.native_agent.agent import Agent

    type AnyAgent = Agent[Any, Any]
    from agentpool_config.nodes import ToolConfirmationMode


TOOL_CONFIRMATION_MODES = [
    ModeInfo(
        value="always",
        name="Always",
        description="Always require confirmation for all tools",
    ),
    ModeInfo(
        value="never",
        name="Never",
        description="Never require confirmation (auto-approve all)",
    ),
    ModeInfo(
        value="per_tool",
        name="Per Tool",
        description="Require confirmation only for tools marked as needing it",
    ),
]


class ToolConfirmationOption(ConfigOption["AnyAgent"]):
    """When tool calls need the user's confirmation."""

    id = "mode"
    name = "Tool Confirmation"
    category = "mode"

    async def choices(self, agent: AnyAgent) -> list[ModeInfo]:
        return TOOL_CONFIRMATION_MODES

    def current(self, agent: AnyAgent) -> str:
        return agent.tool_confirmation_mode

    async def _apply(self, agent: AnyAgent, value: str) -> None:
        agent.tool_confirmation_mode = cast("ToolConfirmationMode", value)


class NativeModelOption(ModelOption["AnyAgent"]):
    """Model selection; accepts any model string pydantic-ai can resolve.

    Set within a session (``agent.use_session(...)``), it is that session's model
    (``SessionOverrides.model``); otherwise the agent's.
    """

    async def _apply(self, agent: AnyAgent, value: str) -> None:
        model, settings = agent._resolve_model_string(value)  # Validates the string
        if (session := agent._explicit_session) is not None:
            session.overrides = session.overrides.model_copy(update={"model": value})
            return
        agent._model = model
        if settings:
            agent.model_settings = settings


CONFIG_OPTIONS: tuple[ConfigOption[AnyAgent], ...] = (ToolConfirmationOption(), NativeModelOption())
"""Options of every native agent, in display order."""
