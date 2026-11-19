"""v2 ``session/request_permission``: asking the user before a sensitive operation.

v2 permission requests carry a required ``title`` and an optional ``subject``: a tool
call (with the same payload as a ``tool_call_update``) or a command.
"""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Annotated, Literal

from pydantic import MISSING, Discriminator, Field, Tag

from acp.schema.v2.base import OTHER_TAG, AnnotatedObject, Unknown, open_discriminator
from acp.schema.v2.common import (  # noqa: TC001
    AbsolutePath,
    PermissionOptionId,
    SessionId,
    TerminalId,
    ToolCallId,
)
from acp.schema.v2.tool_calls import ToolCallUpdate  # noqa: TC001


PermissionOptionKind = Literal["allow_once", "allow_always", "reject_once", "reject_always"] | str
"""Nature of a permission option, for icons and UI. Values starting with ``_`` are extensions."""


class PermissionOption(AnnotatedObject):
    """An option offered to the user."""

    option_id: PermissionOptionId
    """Identifier of the option; unique within the request."""

    name: str
    """Label shown to the user."""

    kind: PermissionOptionKind
    """Hint about the nature of the option."""


class ToolCallPermissionSubject(AnnotatedObject):
    """Permission is requested before executing a tool call."""

    type: Literal["tool_call"] = Field(default="tool_call", init=False)

    tool_call: ToolCallUpdate
    """The tool call; clients apply it to their stored tool call like a ``tool_call_update``."""


class CommandPermissionSubject(AnnotatedObject):
    """Permission is requested before running a command."""

    type: Literal["command"] = Field(default="command", init=False)

    command: str
    """The command that runs if permission is granted."""

    cwd: AbsolutePath
    """Absolute working directory of the command."""

    tool_call_id: ToolCallId | MISSING | None = MISSING
    """The associated tool call, when known."""

    terminal_id: TerminalId | MISSING | None = MISSING
    """The associated terminal, when already known."""


class UnknownPermissionSubject(Unknown):
    """A custom or future subject; ``type`` values starting with ``_`` are extensions."""

    type: str
    """The subject kind."""


RequestPermissionSubject = Annotated[
    Annotated[ToolCallPermissionSubject, Tag("tool_call")]
    | Annotated[CommandPermissionSubject, Tag("command")]
    | Annotated[UnknownPermissionSubject, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("type", frozenset({"tool_call", "command"}))),
]
"""The operation that requires permission."""


class RequestPermissionRequest(AnnotatedObject):
    """Parameters of ``session/request_permission``."""

    session_id: SessionId
    """The session asking."""

    title: str
    """Title of the permission prompt (not the title of a tool call)."""

    description: str | MISSING | None = MISSING
    """Why permission is needed."""

    subject: RequestPermissionSubject | MISSING | None = MISSING
    """Structured context about the operation."""

    options: Sequence[PermissionOption] = Field(min_length=1)
    """The options the user can choose from; at least one."""


class SelectedPermissionOutcome(AnnotatedObject):
    """The user selected one of the offered options.

    An ``option_id`` that matches no offered option counts as rejection.
    """

    outcome: Literal["selected"] = Field(default="selected", init=False)

    option_id: PermissionOptionId
    """The selected option."""


class CancelledPermissionOutcome(AnnotatedObject):
    """No option was selected, e.g. because the session's work was cancelled."""

    outcome: Literal["cancelled"] = Field(default="cancelled", init=False)


class UnknownPermissionOutcome(Unknown):
    """A custom or future outcome; values starting with ``_`` are extensions."""

    outcome: str
    """The outcome kind."""


RequestPermissionOutcome = Annotated[
    Annotated[CancelledPermissionOutcome, Tag("cancelled")]
    | Annotated[SelectedPermissionOutcome, Tag("selected")]
    | Annotated[UnknownPermissionOutcome, Tag(OTHER_TAG)],
    Discriminator(open_discriminator("outcome", frozenset({"cancelled", "selected"}))),
]
"""The user's decision on a permission request."""


class RequestPermissionResponse(AnnotatedObject):
    """Response to ``session/request_permission``."""

    outcome: RequestPermissionOutcome
    """The user's decision."""
