"""Default ACP client implementation.

This module provides a basic client implementation for the Agent Client Protocol (ACP)
that can be used for testing or as a base for more sophisticated client implementations.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from acp.client import Client
from acp.schema import ReadTextFileResponse, RequestPermissionResponse, WriteTextFileResponse


if TYPE_CHECKING:
    from acp.schema import (
        AllowedOutcome,
        CreateTerminalRequest,
        CreateTerminalResponse,
        DeniedOutcome,
        KillTerminalRequest,
        KillTerminalResponse,
        ReadTextFileRequest,
        ReleaseTerminalRequest,
        ReleaseTerminalResponse,
        RequestPermissionRequest,
        SessionNotification,
        TerminalOutputRequest,
        TerminalOutputResponse,
        WaitForTerminalExitRequest,
        WaitForTerminalExitResponse,
        WriteTextFileRequest,
    )
    from acp.schema.elicitation import (
        ElicitationCompleteNotification,
        ElicitationRequest,
        ElicitationResponse,
    )

logger = structlog.get_logger(__name__)


class DefaultACPClient(Client):
    """Default implementation of ACP Client interface for basic operations.

    This provides a basic client implementation that can be used for testing
    or as a base for more sophisticated client implementations.
    """

    def __init__(
        self,
        *,
        allow_file_operations: bool = False,
        permission_outcomes: list[dict[str, DeniedOutcome | AllowedOutcome]] | None = None,
        use_real_files: bool = True,
    ) -> None:
        """Initialize default ACP client.

        Args:
            allow_file_operations: Whether to allow file read/write operations
            permission_outcomes: Queue of permission outcomes for testing
            use_real_files: Whether to use real filesystem or in-memory storage
        """
        self.allow_file_operations = allow_file_operations
        self.use_real_files = use_real_files
        self.permission_outcomes = permission_outcomes or []
        self.files: dict[str, str] = {}  # In-memory file storage for testing
        self.ext_calls: list[tuple[str, dict[str, Any]]] = []
        self.ext_notes: list[tuple[str, dict[str, Any]]] = []
        self.notifications: list[SessionNotification] = []

    async def request_permission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse:
        """Default permission handler - grants all permissions or uses test queue."""
        title = params.tool_call.title or "operation"
        logger.info("Permission requested", title=title)

        # If we have test outcomes queued, use them
        if self.permission_outcomes:
            outcome = self.permission_outcomes.pop(0)
            return RequestPermissionResponse.model_validate({"outcome": outcome})

        # Default: grant permission for the first option
        if params.options:
            id_ = params.options[0].option_id
            return RequestPermissionResponse.allowed(id_)

        # No options - deny
        return RequestPermissionResponse.denied()

    async def session_update(self, params: SessionNotification) -> None:
        """Handle session update notifications."""
        msg = "Session update for %s: %s"
        logger.debug(msg, params.session_id, params.update.session_update)
        self.notifications.append(params)

    async def write_text_file(self, params: WriteTextFileRequest) -> WriteTextFileResponse:
        """Write text to file (if allowed)."""
        if not self.allow_file_operations:
            raise RuntimeError("File operations not allowed")

        if self.use_real_files:
            try:
                path = Path(params.path)
                path.write_text(params.content, encoding="utf-8")
                logger.info("Wrote file", path=params.path)
            except Exception:
                logger.exception("Failed to write file", path=params.path)
                raise
        else:
            # In-memory storage for testing
            self.files[str(params.path)] = params.content

        return WriteTextFileResponse()

    async def read_text_file(self, params: ReadTextFileRequest) -> ReadTextFileResponse:
        """Read text from file (if allowed)."""
        if not self.allow_file_operations:
            raise RuntimeError("File operations not allowed")

        if self.use_real_files:
            try:
                path = Path(params.path)

                if not path.exists():
                    raise FileNotFoundError(f"File not found: {params.path}")  # noqa: TRY301

                content = path.read_text(encoding="utf-8")

                # Apply line filtering if requested
                if params.line is not None or params.limit is not None:
                    lines = content.splitlines()
                    start_line = (params.line - 1) if params.line else 0
                    end_line = start_line + params.limit if params.limit else len(lines)
                    content = "\n".join(lines[start_line:end_line])

                logger.info("Read file", path=params.path)
                return ReadTextFileResponse(content=content)

            except Exception:
                logger.exception("Failed to read file", path=params.path)
                raise
        else:
            # In-memory storage for testing
            content = self.files.get(str(params.path), "default content")
            return ReadTextFileResponse(content=content)

    async def create_terminal(self, params: CreateTerminalRequest) -> CreateTerminalResponse:
        """Create terminal (not implemented)."""
        raise NotImplementedError("Terminal operations not implemented")

    async def terminal_output(self, params: TerminalOutputRequest) -> TerminalOutputResponse:
        """Get terminal output (not implemented)."""
        raise NotImplementedError("Terminal operations not implemented")

    async def release_terminal(
        self, params: ReleaseTerminalRequest
    ) -> ReleaseTerminalResponse | None:
        """Release terminal (not implemented)."""
        raise NotImplementedError("Terminal operations not implemented")

    async def wait_for_terminal_exit(
        self, params: WaitForTerminalExitRequest
    ) -> WaitForTerminalExitResponse:
        """Wait for terminal exit (not implemented)."""
        raise NotImplementedError("Terminal operations not implemented")

    async def kill_terminal(self, params: KillTerminalRequest) -> KillTerminalResponse | None:
        """Kill terminal (not implemented)."""
        raise NotImplementedError("Terminal operations not implemented")

    def get_session_updates(self) -> list[SessionNotification]:
        """Get all received session updates."""
        return self.notifications.copy()

    def clear_session_updates(self) -> None:
        """Clear all stored session updates."""
        self.notifications.clear()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.ext_calls.append((method, params))
        return {"ok": True, "method": method}

    async def elicitation(self, params: ElicitationRequest) -> ElicitationResponse:
        """Decline elicitation by default."""
        from acp.schema.elicitation import ElicitationResponse

        return ElicitationResponse.decline()

    async def elicitation_complete(self, params: ElicitationCompleteNotification) -> None:
        """Ignore elicitation complete notifications."""

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        self.ext_notes.append((method, params))
