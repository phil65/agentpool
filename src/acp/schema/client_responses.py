"""Client response schema definitions."""

from __future__ import annotations

from typing import Any, Self

from acp.schema.base import Response
from acp.schema.elicitation import ElicitationResponse
from acp.schema.mcp import MessageMcpResponse
from acp.schema.terminal import TerminalExitStatus  # noqa: TC001
from acp.schema.tool_call import AllowedOutcome, DeniedOutcome


# terminal


class CreateTerminalResponse(Response):
    """Response containing the ID of the created terminal."""

    terminal_id: str
    """The unique identifier for the created terminal."""


class KillTerminalResponse(Response):
    """Response to the ``terminal/kill`` method."""


class ReleaseTerminalResponse(Response):
    """Response to terminal/release method."""


class TerminalOutputResponse(Response):
    """Response containing the terminal output and exit status."""

    exit_status: TerminalExitStatus | None = None
    """Exit status if the command has completed."""

    output: str
    """The terminal output captured so far."""

    truncated: bool
    """Whether the output was truncated due to byte limits."""


class WaitForTerminalExitResponse(Response):
    """Response containing the exit status of a terminal command."""

    exit_code: int | None = None
    """The process exit code (may be null if terminated by signal)."""

    signal: str | None = None
    """The signal that terminated the process (may be null if exited normally)."""


# Filesystem


class WriteTextFileResponse(Response):
    """Response to `fs/write_text_file`."""


class ReadTextFileResponse(Response):
    """Response containing the contents of a text file."""

    content: str
    """The contents of the text file."""


# permissions


class RequestPermissionResponse(Response):
    """Response to a permission request."""

    outcome: DeniedOutcome | AllowedOutcome
    """The user's decision on the permission request."""

    @classmethod
    def denied(cls, metadata: dict[str, Any] | None = None) -> Self:
        """Create a response indicating that the permission request was denied."""
        return cls(outcome=DeniedOutcome(), field_meta=metadata)

    @classmethod
    def allowed(cls, option_id: str, metadata: dict[str, Any] | None = None) -> Self:
        """Create a response indicating that the permission request was allowed."""
        return cls(outcome=AllowedOutcome(option_id=option_id), field_meta=metadata)


ClientResponse = (
    WriteTextFileResponse
    | ReadTextFileResponse
    | RequestPermissionResponse
    | CreateTerminalResponse
    | TerminalOutputResponse
    | ReleaseTerminalResponse
    | WaitForTerminalExitResponse
    | KillTerminalResponse
    | ElicitationResponse
    | MessageMcpResponse
)
