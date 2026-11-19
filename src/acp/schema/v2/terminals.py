"""v2 agent-owned, display-only terminals.

Agents run commands themselves; terminals only display them. Tool calls reference a
terminal by ``terminalId`` (``Terminal`` content); ``terminal_update`` upserts carry
command, cwd, replay snapshots and exit status, and ``terminal_output_chunk`` appends
independently base64-encoded bytes. Clients cannot execute or control processes,
except cancelling terminals that allow it.
"""

from __future__ import annotations

import base64
from typing import Literal, Self

from pydantic import MISSING, Field

from acp.schema.v2.base import AnnotatedObject
from acp.schema.v2.common import AbsolutePath, SessionId, TerminalId  # noqa: TC001


class TerminalOutput(AnnotatedObject):
    """An authoritative replacement snapshot of terminal output bytes."""

    data: str
    """Base64-encoded replacement terminal output bytes."""

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        """Create a snapshot from raw output bytes."""
        return cls(data=base64.b64encode(data).decode("ascii"))

    def decode(self) -> bytes:
        """The raw output bytes."""
        return base64.b64decode(self.data)


class TerminalSize(AnnotatedObject):
    """The size of an agent-owned terminal in character cells."""

    cols: int = Field(ge=1, le=65535)
    """Number of columns."""

    rows: int = Field(ge=1, le=65535)
    """Number of rows."""


class TerminalExitStatus(AnnotatedObject):
    """Exit information for an agent-owned terminal.

    Its presence marks the terminal as exited, even when neither code nor signal is known.
    """

    exit_code: int | MISSING | None = MISSING
    """Process exit code as the platform reports it; negative values are allowed."""

    signal: str | MISSING | None = MISSING
    """Signal that terminated the process, when known."""


class TerminalCancelCapabilities(AnnotatedObject):
    """Capability to cancel a terminal with ``terminal/cancel``."""


class TerminalCapabilities(AnnotatedObject):
    """Client-initiated controls a terminal currently supports."""

    cancel: TerminalCancelCapabilities | MISSING | None = MISSING
    """Permits the Client to cancel this terminal with ``terminal/cancel``."""


class TerminalUpdate(AnnotatedObject):
    """An upsert of an agent-owned terminal's state, keyed by ``terminal_id``.

    Other fields are patches: omitted leaves the stored value unchanged, ``None``
    clears it.
    """

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

    @classmethod
    def from_bytes(cls, terminal_id: TerminalId, data: bytes) -> Self:
        """Create a chunk from raw output bytes."""
        return cls(terminal_id=terminal_id, data=base64.b64encode(data).decode("ascii"))

    def decode(self) -> bytes:
        """The raw output bytes."""
        return base64.b64decode(self.data)


class CancelTerminalRequest(AnnotatedObject):
    """Parameters of ``terminal/cancel``: cancel one terminal.

    Only for terminals that currently advertise ``capabilities.cancel``.
    """

    session_id: SessionId
    """The ID of the session that owns the terminal."""

    terminal_id: TerminalId
    """The ID of the terminal to cancel."""


class CancelTerminalResponse(AnnotatedObject):
    """Response from cancelling a terminal."""
