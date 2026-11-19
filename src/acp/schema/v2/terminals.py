"""v2 agent-owned, display-only terminals.

Agents run commands themselves and show them as terminals: ``terminal_update`` upserts
a terminal's state (command, absolute cwd, output snapshot, exit status), and
``terminal_output_chunk`` appends independently base64-encoded output bytes. Tool
calls reference terminals by ``terminalId``. There are no client execution methods.
"""

from __future__ import annotations

from typing import Literal

from pydantic import MISSING, Field

from acp.schema.v2.base import AnnotatedObject
from acp.schema.v2.common import AbsolutePath, SessionId, TerminalId  # noqa: TC001


class TerminalOutput(AnnotatedObject):
    """An authoritative replacement snapshot of terminal output bytes."""

    data: str
    """Base64-encoded replacement terminal output bytes."""


class TerminalSize(AnnotatedObject):
    """The size of an agent-owned terminal in character cells."""

    cols: int = Field(ge=1, le=65535)
    """Number of columns."""

    rows: int = Field(ge=1, le=65535)
    """Number of rows."""


class TerminalExitStatus(AnnotatedObject):
    """Exit information for an agent-owned terminal."""

    exit_code: int | MISSING | None = MISSING
    """Process exit code as the platform reports it, when known. Negative values are allowed,
    for example signed Windows exit codes. Omitted and ``null`` are equivalent."""

    signal: str | MISSING | None = MISSING
    """Signal that terminated the process, when known."""


class TerminalCancelCapabilities(AnnotatedObject):
    """Capability to cancel a terminal with ``terminal/cancel``."""


class TerminalCapabilities(AnnotatedObject):
    """Client-initiated controls a terminal currently supports."""

    cancel: TerminalCancelCapabilities | MISSING | None = MISSING
    """Permits the Client to cancel this terminal with ``terminal/cancel``."""


class TerminalUpdate(AnnotatedObject):
    """An upsert for the stored state of an agent-owned terminal."""

    session_update: Literal["terminal_update"] = Field(default="terminal_update", init=False)

    terminal_id: TerminalId
    """Unique identifier for this terminal within the session."""

    command: str | MISSING | None = MISSING
    """The command being run."""

    cwd: AbsolutePath | MISSING | None = MISSING
    """The absolute working directory of the command."""

    output: TerminalOutput | MISSING | None = MISSING
    """An authoritative replacement snapshot of terminal output bytes."""

    exit_status: TerminalExitStatus | MISSING | None = MISSING
    """Exit information. A concrete object marks the terminal as exited."""

    size: TerminalSize | MISSING | None = MISSING
    """The terminal size the command runs with, when known."""

    capabilities: TerminalCapabilities | MISSING | None = MISSING
    """Client-initiated controls this terminal currently supports, such as cancellation."""


class TerminalOutputChunk(AnnotatedObject):
    """A chunk of bytes appended to an agent-owned terminal's output."""

    session_update: Literal["terminal_output_chunk"] = Field(
        default="terminal_output_chunk", init=False
    )

    terminal_id: TerminalId
    """The terminal receiving these bytes."""

    data: str
    """Independently base64-encoded terminal output bytes."""


class CancelTerminalRequest(AnnotatedObject):
    """Request parameters for cancelling one terminal."""

    session_id: SessionId
    """The ID of the session that owns the terminal."""

    terminal_id: TerminalId
    """The ID of the terminal to cancel."""


class CancelTerminalResponse(AnnotatedObject):
    """Response from cancelling a terminal."""
