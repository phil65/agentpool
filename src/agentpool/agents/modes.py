"""Mode definitions for agent behavior configuration.

Modes represent switchable behaviors/configurations that agents can expose
to clients. Each agent type can define its own mode categories.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from agentpool.agents.exceptions import UnknownModeError


if TYPE_CHECKING:
    from agentpool.agents.base_agent import BaseAgent


# Standard config option IDs aligned with ACP's SessionConfigOptionCategory.
# See: src/acp/schema/session_state.py
ModeCategoryId = Literal[
    "mode",  # Session mode / permissions / approval policy
    "model",  # Model selection
    "thought_level",  # Thinking/reasoning effort level
    "effort",  # Reasoning effort level (low/medium/high/max)
    "personality",  # Personality preset
]


@dataclass
class ModeInfo:
    """Information about a single mode option.

    Represents one selectable option within a mode category.
    """

    value: str | bool
    """Value for this mode."""

    name: str
    """Human-readable display name."""

    description: str = ""
    """Optional description of what this mode does."""

    category_id: ModeCategoryId | str = ""
    """ID of the category this mode belongs to."""


@dataclass
class ConfigOptionChanged:
    """Atomic state change for a config option.

    Emitted by agents when a config option value changes.
    The ACP layer converts this to ACP's ConfigOptionUpdate.
    """

    config_id: str
    """ID of the config option that changed (e.g., 'permissions', 'model')."""

    value_id: str | bool
    """New value ID for this config option."""

    session_id: str | None = None
    """The session whose option changed; None for the agent's own (every session's).

    Agents that run sessions concurrently keep options per session: a change of one
    session's is for that session only.
    """


@dataclass
class ModeCategory:
    """Data container for mode category information.

    Used by get_modes() to expose available modes to clients; built from a
    :class:`ConfigOption` by :meth:`ConfigOption.describe`.

    Examples:
        - Permissions: default, acceptEdits
        - Behavior: plan, code, architect

    TODO: Consider adding an `is_static` field to indicate whether modes are
    known in advance (Codex, Claude Code) vs dynamic (ACP where modes can
    change based on model selection). This would help consumers like OpenCode
    decide whether to fetch modes once at startup or handle updates.
    """

    id: ModeCategoryId | str
    """Unique identifier for this category."""

    name: str
    """Human-readable display name for the category."""

    available_modes: list[ModeInfo] = field(default_factory=list)
    """List of available modes in this category."""

    current_mode_id: str = ""
    """ID of the currently active mode."""

    category: Literal["mode", "model", "thought_level", "other"] | None = None
    """Optional semantic category for UX purposes (keyboard shortcuts, icons, placement).

    This helps clients distinguish common selector types:
    - 'mode': Session mode selector
    - 'model': Model selector
    - 'thought_level': Thought/reasoning level selector
    - 'other': Unknown/uncategorized

    MUST NOT be required for correctness. Clients should handle gracefully.
    """


OptionCategory = Literal["mode", "model", "thought_level", "other"]
"""Semantic category of an option, for UX (see :attr:`ModeCategory.category`)."""


class ConfigOption[AgentT: BaseAgent[Any, Any]](ABC):
    """A switchable agent setting that describes and applies itself.

    Agents declare their options in :attr:`BaseAgent.config_options`; ``get_modes()``
    and ``set_mode()`` work from that list, so each option is defined once. The state
    stays on the agent: an option reads it in :meth:`current` and changes it in
    :meth:`_apply`.
    """

    id: ClassVar[str]
    """Unique identifier of the option within an agent (used as config ID)."""

    name: ClassVar[str]
    """Human-readable display name."""

    category: ClassVar[OptionCategory | None] = None
    """Optional semantic category for UX purposes."""

    strict: ClassVar[bool] = True
    """Whether values must be one of :meth:`choices`."""

    @abstractmethod
    async def choices(self, agent: AgentT) -> list[ModeInfo]:
        """The selectable values; an option without choices is not offered."""

    @abstractmethod
    def current(self, agent: AgentT) -> str:
        """The current value ID, or ``""`` if unset."""

    @abstractmethod
    async def _apply(self, agent: AgentT, value: str) -> None:
        """Change the agent's state (and its backend, if needed) to ``value``."""

    async def describe(self, agent: AgentT) -> ModeCategory | None:
        """The option as a :class:`ModeCategory`, or ``None`` if it has no choices."""
        if not (choices := await self.choices(agent)):
            return None
        modes = [replace(c, category_id=self.id) for c in choices]
        return ModeCategory(
            id=self.id,
            name=self.name,
            available_modes=modes,
            current_mode_id=self.current(agent) or str(modes[0].value),
            category=self.category,
        )

    async def apply(self, agent: AgentT, value: str | bool) -> None:
        """Validate ``value``, apply it and announce the change."""
        if not isinstance(value, str):
            raise UnknownModeError(value, [str(c.value) for c in await self.choices(agent)])
        if self.strict:
            valid = [str(c.value) for c in await self.choices(agent)]
            if value not in valid:
                raise UnknownModeError(value, valid)
        await self._apply(agent, value)
        await agent.update_state(config_id=self.id, value_id=value)


class ModelOption[AgentT: BaseAgent[Any, Any]](ConfigOption[AgentT]):
    """Model selection: choices from ``get_available_models()``.

    Not strict by default, since agents usually accept models outside their list
    (e.g. any ``provider:model`` string). Subclasses implement :meth:`_apply`.
    """

    id = "model"
    name = "Model"
    category = "model"
    strict = False

    async def choices(self, agent: AgentT) -> list[ModeInfo]:
        models = await agent.get_available_models() or []
        return [
            ModeInfo(
                value=m.id_override or m.id,
                name=m.name or m.id,
                description=m.description or "",
            )
            for m in models
        ]

    def current(self, agent: AgentT) -> str:
        return agent.model_name or ""
