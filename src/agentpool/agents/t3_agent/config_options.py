"""Config options of the T3 agent: they apply to threads it launches from then on."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from agentpool.agents.modes import ConfigOption, ModeInfo, ModelOption


if TYPE_CHECKING:
    from agentpool.agents.t3_agent.t3_agent import T3Agent
    from agentpool.models.t3_agents import T3InteractionMode, T3RuntimeMode

    type AnyT3Agent = T3Agent[Any]


RUNTIME_MODES = [
    ModeInfo(
        value="approval-required",
        name="Supervised",
        description="Asks before commands and edits (approve in T3 Code)",
    ),
    ModeInfo(value="auto-accept-edits", name="Auto-accept edits", description="Edits run freely"),
    ModeInfo(value="auto", name="Auto", description="Runs without asking"),
    ModeInfo(value="full-access", name="Full access", description="No sandbox"),
]

INTERACTION_MODES = [
    ModeInfo(value="default", name="Default", description="Carry out the request"),
    ModeInfo(value="plan", name="Plan", description="Propose a plan first"),
]


class RuntimeModeOption(ConfigOption["AnyT3Agent"]):
    """Permission mode of the T3 thread."""

    id = "mode"
    name = "Mode"
    category = "mode"

    async def choices(self, agent: AnyT3Agent) -> list[ModeInfo]:
        return RUNTIME_MODES

    def current(self, agent: AnyT3Agent) -> str:
        return agent._runtime_mode

    async def _apply(self, agent: AnyT3Agent, value: str) -> None:
        agent._runtime_mode = cast("T3RuntimeMode", value)


class InteractionModeOption(ConfigOption["AnyT3Agent"]):
    """Whether the T3 thread plans first."""

    id = "interaction"
    name = "Interaction"
    category = "other"

    async def choices(self, agent: AnyT3Agent) -> list[ModeInfo]:
        return INTERACTION_MODES

    def current(self, agent: AnyT3Agent) -> str:
        return agent._interaction_mode

    async def _apply(self, agent: AnyT3Agent, value: str) -> None:
        agent._interaction_mode = cast("T3InteractionMode", value)


class T3ModelOption(ModelOption["AnyT3Agent"]):
    """Provider and model, as ``provider/model``."""

    async def _apply(self, agent: AnyT3Agent, value: str) -> None:
        provider, sep, model = value.partition("/")
        if not sep or not provider or not model:
            msg = f"T3 models are given as provider/model, not {value!r}"
            raise ValueError(msg)
        agent._provider, agent._model = provider, model


CONFIG_OPTIONS: tuple[ConfigOption[AnyT3Agent], ...] = (
    RuntimeModeOption(),
    InteractionModeOption(),
    T3ModelOption(),
)
"""Options of every T3 agent, in display order."""
