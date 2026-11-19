"""v2 session config options: selectors and toggles, and setting their values.

v2 has no dedicated modes or model selector API: agents expose such state as config
options (``category: "mode"`` / ``"model"``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import MISSING, Discriminator, Field, Tag

from acp.schema.v2.base import OTHER_TAG, AnnotatedObject, Unknown, open_discriminator
from acp.schema.v2.common import (  # noqa: TC001
    SessionConfigGroupId,
    SessionConfigId,
    SessionConfigValueId,
    SessionId,
)


SessionConfigOptionCategory = (
    Literal["mode", "model", "model_config", "speed", "thought_level"] | str
)
"""Semantic category of a config option, for UX only.

Clients MUST handle unknown categories gracefully; values starting with ``_`` are
extensions.
"""


class SessionConfigSelectOption(AnnotatedObject):
    """A possible value of a select config option."""

    value: SessionConfigValueId
    """Unique identifier of this value."""

    name: str
    """Human-readable label."""

    description: str | MISSING | None = MISSING
    """Description of this value."""


class SessionConfigSelectGroup(AnnotatedObject):
    """A group of possible values of a select config option."""

    group_id: SessionConfigGroupId
    """Unique identifier of this group."""

    name: str
    """Human-readable label."""

    options: Sequence[SessionConfigSelectOption] = Field(min_length=1)
    """The values in this group."""


SessionConfigSelectOptions = (
    Sequence[SessionConfigSelectOption] | Sequence[SessionConfigSelectGroup]
)
"""Values of a select config option: a flat list, or groups under headers."""


class _ConfigOptionBase(AnnotatedObject):
    config_id: SessionConfigId
    """Unique identifier of the config option."""

    name: str
    """Human-readable label."""

    description: str | MISSING | None = MISSING
    """Description for the Client to show."""

    category: SessionConfigOptionCategory | MISSING | None = MISSING
    """Semantic category, for UX only."""


class SelectSessionConfigOption(_ConfigOptionBase):
    """A single-value selector (dropdown)."""

    type: Literal["select"] = Field(default="select", init=False)

    current_value: SessionConfigValueId
    """The selected value; one of the ``value``s in ``options``."""

    options: SessionConfigSelectOptions
    """The selectable values; at least one, each ``value`` unique."""


class BooleanSessionConfigOption(_ConfigOptionBase):
    """An on/off toggle."""

    type: Literal["boolean"] = Field(default="boolean", init=False)

    current_value: bool
    """The current value."""


class UnknownSessionConfigOption(Unknown, _ConfigOptionBase):
    """A custom or future option kind; ``type`` values starting with ``_`` are extensions."""

    type: str
    """The option kind."""


SessionConfigOption = Annotated[
    Annotated[SelectSessionConfigOption, Tag("select")]
    | Annotated[BooleanSessionConfigOption, Tag("boolean")]
    | Annotated[UnknownSessionConfigOption, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("type", frozenset({"select", "boolean"}))),
]
"""A session config option and its current state."""


_VALUE_TYPES = frozenset({"id", "boolean"})


class _InitialConfigOptionBase(AnnotatedObject):
    config_id: SessionConfigId
    """The config option to set."""


class InitialConfigOptionId(_InitialConfigOptionBase):
    """**UNSTABLE**: Initial value of a select config option."""

    type: Literal["id"] = Field(default="id", init=False)

    value: SessionConfigValueId
    """The value to select."""


class InitialConfigOptionBoolean(_InitialConfigOptionBase):
    """**UNSTABLE**: Initial value of a boolean config option."""

    type: Literal["boolean"] = Field(default="boolean", init=False)

    value: bool
    """The value to set."""


class InitialConfigOptionUnknown(Unknown, _InitialConfigOptionBase):
    """**UNSTABLE**: Initial value of a custom or future kind of config option."""

    type: str
    """The value kind."""

    value: Any
    """The value to set."""


InitialSessionConfigOption = Annotated[
    Annotated[InitialConfigOptionId, Tag("id")]
    | Annotated[InitialConfigOptionBoolean, Tag("boolean")]
    | Annotated[InitialConfigOptionUnknown, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("type", _VALUE_TYPES)),
]
"""**UNSTABLE**: An initial config option value, sent on session setup requests.

Unlike ``session/set_config_option``, an unknown option or value does not fail the
request: the agent ignores it, and the response's ``configOptions`` show the result.
"""


class _SetConfigOptionBase(AnnotatedObject):
    session_id: SessionId
    """The session to set the config option for."""

    config_id: SessionConfigId
    """The config option to set."""


class SetConfigOptionIdRequest(_SetConfigOptionBase):
    """Select a value of a select config option."""

    type: Literal["id"] = Field(default="id", init=False)

    value: SessionConfigValueId
    """The value to select."""


class SetConfigOptionBooleanRequest(_SetConfigOptionBase):
    """Set a boolean config option."""

    type: Literal["boolean"] = Field(default="boolean", init=False)

    value: bool
    """The value to set."""


class SetConfigOptionUnknownRequest(Unknown, _SetConfigOptionBase):
    """Set a custom or future kind of config option."""

    type: str
    """The value kind."""

    value: Any
    """The value to set."""


SetSessionConfigOptionRequest = Annotated[
    Annotated[SetConfigOptionIdRequest, Tag("id")]
    | Annotated[SetConfigOptionBooleanRequest, Tag("boolean")]
    | Annotated[SetConfigOptionUnknownRequest, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("type", _VALUE_TYPES)),
]
"""Parameters of ``session/set_config_option``.

Agents MUST support it for any session where they advertise config options, and
reject unknown options or values with ``-32602``.
"""


class SetSessionConfigOptionResponse(AnnotatedObject):
    """Response to ``session/set_config_option``."""

    config_options: Sequence[SessionConfigOption]
    """All config options and their current values."""
