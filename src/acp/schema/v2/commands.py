"""v2 available commands: slash commands and skills the agent advertises."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import MISSING, Discriminator, Field, Tag

from acp.schema.v2.base import OTHER_TAG, AnnotatedObject, Unknown, open_discriminator
from acp.schema.v2.common import AbsolutePath  # noqa: TC001


class TextCommandInput(AnnotatedObject):
    """All text typed after the command name (and the following whitespace) is the input."""

    type: Literal["text"] = Field(default="text", init=False)

    hint: str
    """Hint to show while no input has been given."""


class UnknownCommandInput(Unknown):
    """A custom or future input kind; ``type`` values starting with ``_`` are extensions."""

    type: str
    """The input kind."""


AvailableCommandInput = Annotated[
    Annotated[TextCommandInput, Tag("text")] | Annotated[UnknownCommandInput, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("type", frozenset({"text"}))),
]
"""How a command takes input."""


class Command(AnnotatedObject):
    """A slash command the user invokes by including ``/name`` in a prompt."""

    type: Literal["command"] = Field(default="command", init=False)

    name: str
    """Command name without the leading ``/``, e.g. ``create_plan``; no whitespace."""

    description: str
    """What the command does."""

    input: AvailableCommandInput | MISSING | None = MISSING
    """The command's input; omitted means it takes none."""


class Skill(AnnotatedObject):
    """**UNSTABLE**: An Agent Skill the user invokes with a ``skill`` content block.

    Agents MUST only send skills to clients that advertise ``session.commands.skill``.
    """

    type: Literal["skill"] = Field(default="skill", init=False)

    name: str
    """Skill name, used in ``skill`` content blocks; no whitespace."""

    description: str
    """What the skill does, shown to users."""

    path: AbsolutePath | MISSING | None = MISSING
    """Absolute path of the file defining the skill (normally its ``SKILL.md``) on the
    agent's machine."""


class UnknownCommand(Unknown):
    """A custom or future command kind; ``type`` values starting with ``_`` are extensions."""

    type: str
    """The command kind."""

    name: str
    """Command name."""

    description: str
    """What the command does."""


AvailableCommand = Annotated[
    Annotated[Command, Tag("command")]
    | Annotated[Skill, Tag("skill")]
    | Annotated[UnknownCommand, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("type", frozenset({"command", "skill"}))),
]
"""A command the agent advertises to the client."""
