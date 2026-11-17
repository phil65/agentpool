"""Headless ACP client implementation with real filesystem and terminal operations.

This module provides a headless client implementation that performs actual
filesystem operations and uses ProcessManager for real terminal execution,
making it ideal for testing and standalone usage.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

from anyenv import ProcessManager
import structlog

from acp.client.protocol import Client
from acp.schema import (
    CreateTerminalResponse,
    KillTerminalResponse,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    TerminalOutputResponse,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)


if TYPE_CHECKING:
    from acp.schema import (
        CreateTerminalRequest,
        KillTerminalRequest,
        ReadTextFileRequest,
        ReleaseTerminalRequest,
        RequestPermissionRequest,
        SessionNotification,
        TerminalOutputRequest,
        WaitForTerminalExitRequest,
        WriteTextFileRequest,
    )
    from acp.schema.elicitation import (
        ElicitationCompleteNotification,
        ElicitationRequest,
        ElicitationResponse,
    )

logger = structlog.get_logger(__name__)


class HeadlessACPClient(Client):
    """Headless ACP client with real filesystem and terminal operations.

    This client implementation:
    - Performs real filesystem operations
    - Uses ProcessManager for actual terminal/command execution
    - Automatically grants permissions for testing
    - Suitable for testing and standalone usage
    """

    def __init__(
        self,
        *,
        working_dir: Path | str | None = None,
        allow_file_operations: bool = True,
        auto_grant_permissions: bool = True,
    ) -> None:
        """Initialize headless ACP client.

        Args:
            working_dir: Default working directory for operations
            allow_file_operations: Whether to allow file read/write operations
            auto_grant_permissions: Whether to automatically grant all permissions
        """
        self.working_dir = Path(working_dir) if working_dir else Path.cwd()
        self.allow_file_operations = allow_file_operations
        self.auto_grant_permissions = auto_grant_permissions
        # Process management for terminals
        self.process_manager = ProcessManager()
        self.terminals: dict[str, str] = {}  # terminal_id -> process_id
        # Tracking for testing/debugging
        self.notifications: list[SessionNotification] = []
        self.permission_requests: list[RequestPermissionRequest] = []

    async def request_permission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse:
        """Handle permission requests. Grants if auto_grant_permissions is True."""
        self.permission_requests.append(params)
        tool_name = params.tool_call.title or "operation"
        logger.info("Permission requested", tool_name=tool_name)
        if self.auto_grant_permissions and params.options:
            # Grant permission using first available option
            option_id = params.options[0].option_id
            logger.debug("Auto-granting permission", tool_name=tool_name)
            return RequestPermissionResponse.allowed(option_id)
        logger.debug("Denying permission", tool_name=tool_name)
        return RequestPermissionResponse.denied()

    async def session_update(self, params: SessionNotification) -> None:
        """Handle session update notifications."""
        typ = type(params.update).__name__
        logger.debug("Session update", session_id=params.session_id, update_type=typ)
        self.notifications.append(params)

    async def read_text_file(self, params: ReadTextFileRequest) -> ReadTextFileResponse:
        """Read text from file."""
        if not self.allow_file_operations:
            raise RuntimeError("File operations not allowed")
        path = Path(params.path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {params.path}")
        try:
            content = path.read_text(encoding="utf-8")
            # Apply line filtering if requested
            if params.line is not None or params.limit is not None:
                lines = content.splitlines(keepends=True)
                start_line = (params.line - 1) if params.line else 0
                end_line = start_line + params.limit if params.limit else len(lines)
                content = "".join(lines[start_line:end_line])
            logger.debug("Read file", path=params.path, num_chars=len(content))
            return ReadTextFileResponse(content=content)

        except Exception:
            logger.exception("Failed to read file", path=params.path)
            raise

    async def write_text_file(self, params: WriteTextFileRequest) -> WriteTextFileResponse:
        """Write text to file."""
        if not self.allow_file_operations:
            raise RuntimeError("File operations not allowed")
        path = Path(params.path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.content, encoding="utf-8")
            logger.debug("Wrote file", path=params.path, num_chars=len(params.content))
            return WriteTextFileResponse()
        except Exception:
            logger.exception("Failed to write file", path=params.path)
            raise

    async def create_terminal(self, params: CreateTerminalRequest) -> CreateTerminalResponse:
        """Create a new terminal session using ProcessManager."""
        try:
            process_id = await self.process_manager.start_process(
                command=params.command,
                args=list(params.args) if params.args else None,
                cwd=params.cwd or str(self.working_dir),
                env={var.name: var.value for var in (params.env or [])},
                output_limit=params.output_byte_limit,
            )
            terminal_id = f"term_{uuid.uuid4().hex[:8]}"
            self.terminals[terminal_id] = process_id
            msg = "Created terminal"
            logger.info(msg, terminal_id=terminal_id, command=params.command, args=params.args)

            return CreateTerminalResponse(terminal_id=terminal_id)

        except Exception:
            logger.exception("Failed to create terminal", command=params.command)
            raise

    async def terminal_output(self, params: TerminalOutputRequest) -> TerminalOutputResponse:
        """Get output from terminal."""
        terminal_id = params.terminal_id
        if terminal_id not in self.terminals:
            raise ValueError(f"Terminal {terminal_id} not found")
        try:
            process_id = self.terminals[terminal_id]
            output = await self.process_manager.get_output(process_id)
            return TerminalOutputResponse(output=output.combined, truncated=output.truncated)
        except Exception:
            logger.exception("Failed to get output", terminal_id=terminal_id)
            raise

    async def wait_for_terminal_exit(
        self, params: WaitForTerminalExitRequest
    ) -> WaitForTerminalExitResponse:
        """Wait for terminal process to exit."""
        terminal_id = params.terminal_id
        if terminal_id not in self.terminals:
            raise ValueError(f"Terminal {terminal_id} not found")
        try:
            process_id = self.terminals[terminal_id]
            exit_code = await self.process_manager.wait_for_exit(process_id)
            logger.debug("Terminal exited", terminal_id=terminal_id, exit_code=exit_code)
            return WaitForTerminalExitResponse(exit_code=exit_code)
        except Exception:
            logger.exception("Failed to wait", terminal_id=terminal_id)
            raise

    async def kill_terminal(self, params: KillTerminalRequest) -> KillTerminalResponse | None:
        """Kill terminal process."""
        terminal_id = params.terminal_id
        if terminal_id not in self.terminals:
            raise ValueError(f"Terminal {terminal_id} not found")
        try:
            process_id = self.terminals[terminal_id]
            await self.process_manager.kill_process(process_id)
            logger.info("Killed terminal", terminal_id=terminal_id)
            return KillTerminalResponse()
        except Exception:
            logger.exception("Failed to kill terminal", terminal_id=terminal_id)
            raise

    async def release_terminal(
        self, params: ReleaseTerminalRequest
    ) -> ReleaseTerminalResponse | None:
        """Release terminal resources."""
        terminal_id = params.terminal_id
        if terminal_id not in self.terminals:
            raise ValueError(f"Terminal {terminal_id} not found")
        try:
            process_id = self.terminals[terminal_id]
            await self.process_manager.release_process(process_id)
            del self.terminals[terminal_id]  # Remove from our tracking
            logger.info("Released terminal", terminal_id=terminal_id)
            return ReleaseTerminalResponse()
        except Exception:
            logger.exception("Failed to release terminal", terminal_id=terminal_id)
            raise

    async def cleanup(self) -> None:
        """Clean up all resources."""
        logger.info("Cleaning up headless client resources")
        for terminal_id, process_id in self.terminals.items():
            try:
                await self.process_manager.release_process(process_id)
            except Exception:
                logger.exception("Error cleaning up terminal", terminal_id=terminal_id)

        self.terminals.clear()
        await self.process_manager.cleanup()
        logger.info("Headless client cleanup completed")

    # Testing/debugging helpers

    def get_session_updates(self) -> list[SessionNotification]:
        """Get all received session updates."""
        return self.notifications.copy()

    def clear_session_updates(self) -> None:
        """Clear all stored session updates."""
        self.notifications.clear()

    def get_permission_requests(self) -> list[RequestPermissionRequest]:
        """Get all permission requests."""
        return self.permission_requests.copy()

    def clear_permission_requests(self) -> None:
        """Clear all stored permission requests."""
        self.permission_requests.clear()

    def list_active_terminals(self) -> list[str]:
        """List all active terminal IDs."""
        return list(self.terminals.keys())

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Handle extension method calls."""
        logger.debug("Extension method called", method=method)
        return {"ok": True, "method": method, "params": params}

    async def elicitation(self, params: ElicitationRequest) -> ElicitationResponse:
        """Decline elicitation by default."""
        from acp.schema.elicitation import ElicitationResponse

        return ElicitationResponse.decline()

    async def elicitation_complete(self, params: ElicitationCompleteNotification) -> None:
        """Ignore elicitation complete notifications."""

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Handle extension notifications."""
        logger.debug("Extension notification", method=method)
