"""Slash command schema definitions."""

from __future__ import annotations

from typing import Self

from pydantic import RootModel

from acp.schema.base import AnnotatedObject


class CommandInputHint(AnnotatedObject):
    """All text that was typed after the command name is provided as input."""

    hint: str
    """A hint to display when the input hasn't been provided yet."""


class AvailableCommandInput(RootModel[CommandInputHint]):
    """A container for the input specification for a command."""

    root: CommandInputHint
    """The input specification for a command."""


class AvailableCommand(AnnotatedObject):
    """Information about a command."""

    description: str
    """Human-readable description of what the command does."""

    input: AvailableCommandInput | None = None
    """Input for the command if required."""

    name: str
    """Command name (e.g., `create_plan`, `research_codebase`)."""

    @classmethod
    def create(cls, name: str, description: str, input_hint: str | None = None) -> Self:
        """Create a new Command.

        Args:
            name: Name of the command.
            description: Description of the command.
            input_hint: Hint for the input.

        Returns:
            A new available command.
        """
        spec = AvailableCommandInput(root=CommandInputHint(hint=input_hint)) if input_hint else None
        return cls(name=name, description=description, input=spec)
