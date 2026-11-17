"""Session state schema definitions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import Discriminator, Field

from acp.schema.base import AnnotatedObject


# Type aliases for config option identifiers
SessionConfigId = str
"""Unique identifier for a configuration option."""

SessionConfigValueId = str
"""Unique identifier for a possible value within a configuration option."""

SessionConfigGroupId = str
"""Unique identifier for a group of values within a configuration option."""


SessionConfigOptionCategory = (
    Literal["mode", "model", "model_config", "speed", "thought_level", "other"] | str
)
"""Semantic category for a session configuration option.

This is intended to help Clients distinguish broadly common selectors (e.g. model selector vs
session mode selector vs thought/reasoning level) for UX purposes (keyboard shortcuts, icons,
placement). It MUST NOT be required for correctness. Clients MUST handle missing or unknown
categories gracefully (treat as `other`).

``speed`` is **UNSTABLE**.
"""


class SessionConfigSelectOption(AnnotatedObject):
    """A possible value for a configuration selector."""

    value: SessionConfigValueId
    """Unique identifier for this option value."""

    name: str
    """Human-readable label for this option value."""

    description: str | None = None
    """Optional description for this option value."""


class SessionConfigSelectGroup(AnnotatedObject):
    """A group of possible values for a configuration selector."""

    group: SessionConfigGroupId
    """Unique identifier for this group."""

    name: str
    """Human-readable label for this group."""

    options: Sequence[SessionConfigSelectOption]
    """The set of option values in this group."""


SessionConfigSelectOptions = (
    Sequence[SessionConfigSelectOption] | Sequence[SessionConfigSelectGroup]
)
"""The possible values for a configuration selector, optionally organized into groups."""


class BaseSessionConfigOption(AnnotatedObject):
    """Base fields shared by all session config option variants."""

    id: SessionConfigId
    """Unique identifier for the configuration option."""

    name: str
    """Human-readable label for the option."""

    description: str | None = None
    """Optional description for the Client to display to the user."""

    category: SessionConfigOptionCategory | None = None
    """Optional semantic category for this option (UX only)."""


class SelectSessionConfigOption(BaseSessionConfigOption):
    """A select-type session configuration option.

    Single-value selector (dropdown) with a list of options.

    Advertised by the agent to describe an available select config option and its current state.
    See ``SessionConfigOptionValueId`` for the value sent by the client when changing it.
    """

    type: Literal["select"] = Field(default="select", init=False)
    """Discriminator for the config option type."""

    current_value: SessionConfigValueId
    """The currently selected value."""

    options: SessionConfigSelectOptions
    """The set of selectable options."""


class BooleanSessionConfigOption(BaseSessionConfigOption):
    """**UNSTABLE**: This capability is not part of the spec yet.

    A boolean on/off toggle session configuration option.

    Advertised by the agent to describe an available boolean config option and its current state.
    See ``SessionConfigOptionValueBoolean`` for the value sent by the client when changing it.
    """

    type: Literal["boolean"] = Field(default="boolean", init=False)
    """Discriminator for the config option type."""

    current_value: bool
    """The current value of the boolean option."""


SessionConfigOption = Annotated[
    SelectSessionConfigOption | BooleanSessionConfigOption,
    Discriminator("type"),
]
"""A session configuration option, discriminated by ``type``.

For ``type: "select"`` the ``options`` and ``current_value`` (string) fields
are present. For ``type: "boolean"`` only ``current_value`` (bool) is present."""


# --- SetSessionConfigOption value types ---


class SessionConfigOptionValueBoolean(AnnotatedObject):
    """A boolean value for setting a config option (type: "boolean").

    Sent by the client to change a boolean config option.
    See ``BooleanSessionConfigOption`` for the option definition advertised by the agent.
    """

    type: Literal["boolean"] = Field(default="boolean", init=False)
    """Discriminator value."""

    value: bool
    """The boolean value."""


class SessionConfigOptionValueId(AnnotatedObject):
    """A SessionConfigValueId string value for setting a config option.

    This is the default when ``type`` is absent on the wire. Unknown ``type``
    values with string payloads also gracefully deserialize into this variant.

    Sent by the client to change a select config option.
    See ``SelectSessionConfigOption`` for the option definition advertised by the agent.
    """

    value: SessionConfigValueId
    """The value ID."""


SessionConfigOptionValue = SessionConfigOptionValueBoolean | SessionConfigOptionValueId
"""The value to set for a session configuration option.

When ``type`` is ``"boolean"``, carries a bool. Otherwise (or when ``type``
is absent), carries a ``SessionConfigValueId`` string.
"""


class InitialSessionConfigOption(AnnotatedObject):
    """**UNSTABLE**: A config option value to apply when a session is set up.

    Sent on ``session/new``, ``session/load``, ``session/resume`` and ``session/fork``.
    When ``type`` is ``"boolean"``, ``value`` is a bool, otherwise a value ID string.
    """

    config_id: SessionConfigId
    """The ID of the configuration option to set."""

    type: Literal["boolean"] | None = None
    """The value type discriminator. ``"boolean"`` for bool values, absent for value IDs."""

    value: SessionConfigValueId | bool
    """The value to set."""
