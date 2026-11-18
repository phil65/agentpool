"""ACP request utilities for simplified client interactions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from acp.schema import (
    CreateTerminalRequest,
    EnvVariable,
    KillTerminalRequest,
    PermissionOption,
    ReadTextFileRequest,
    ReleaseTerminalRequest,
    RequestPermissionRequest,
    TerminalOutputRequest,
    ToolCallUpdate,
    WaitForTerminalExitRequest,
    WriteTextFileRequest,
)
from acp.terminal_handle import TerminalHandle


if TYPE_CHECKING:
    from collections.abc import Sequence

    from acp import (
        Client,
        ElicitationRequest,
        ElicitationResponse,
        RequestPermissionResponse,
        TerminalOutputResponse,
        WaitForTerminalExitResponse,
    )

logger = structlog.get_logger(__name__)


class ACPRequests:
    """Clean API for creating and sending ACP session requests.

    Provides convenient methods for common request patterns,
    handling both creation and sending in a single call.
    """

    def __init__(self, client: Client, session_id: str) -> None:
        """Initialize requests helper."""
        self.client = client
        self.id = session_id

    async def read_text_file(
        self,
        path: str,
        *,
        limit: int | None = None,
        line: int | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Read text content from a file.

        Args:
            path: File path to read
            limit: Maximum number of lines to read
            line: Line number to start reading from (1-based)
            metadata: Optional metadata to include with the request

        Returns:
            File content as string
        """
        request = ReadTextFileRequest(
            session_id=self.id,
            path=path,
            limit=limit,
            line=line,
            field_meta=metadata,
        )
        response = await self.client.read_text_file(request)
        return response.content

    async def write_text_file(
        self, path: str, content: str, metadata: dict[str, str] | None = None
    ) -> None:
        """Write text content to a file."""
        request = WriteTextFileRequest(
            session_id=self.id,
            path=path,
            content=content,
            field_meta=metadata,
        )
        await self.client.write_text_file(request)

    async def create_terminal(
        self,
        command: str,
        *,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        output_byte_limit: int | None = None,
        metadata: dict[str, str] | None = None,
    ) -> TerminalHandle:
        """Create a new terminal session.

        Args:
            command: Command to run. For shell commands (pipes, redirects, etc.),
                pass the full command string and omit args.
            args: Command arguments, appended to command.
            cwd: Working directory for terminal
            env: Environment variables for terminal
            output_byte_limit: Maximum bytes to capture from output
            metadata: Optional metadata to include with the request
        """
        request = CreateTerminalRequest(
            session_id=self.id,
            command=command,
            # Only include args if non-empty, following claude-agent-acp pattern.
            # When args is None/empty, ACP clients interpret command as a shell command.
            args=args or None,
            cwd=cwd,
            env=[EnvVariable(name=k, value=v) for k, v in (env or {}).items()],
            output_byte_limit=output_byte_limit,
            field_meta=metadata,
        )
        response = await self.client.create_terminal(request)
        return TerminalHandle(terminal_id=response.terminal_id, requests=self)

    async def terminal_output(
        self,
        terminal_id: str,
        metadata: dict[str, str] | None = None,
    ) -> TerminalOutputResponse:
        """Get output from a terminal session."""
        request = TerminalOutputRequest(
            session_id=self.id,
            terminal_id=terminal_id,
            field_meta=metadata,
        )
        return await self.client.terminal_output(request)

    async def wait_for_terminal_exit(
        self,
        terminal_id: str,
        metadata: dict[str, str] | None = None,
    ) -> WaitForTerminalExitResponse:
        """Wait for a terminal to exit."""
        request = WaitForTerminalExitRequest(
            session_id=self.id,
            terminal_id=terminal_id,
            field_meta=metadata,
        )
        return await self.client.wait_for_terminal_exit(request)

    async def kill_terminal(
        self,
        terminal_id: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Kill a terminal session."""
        request = KillTerminalRequest(
            session_id=self.id,
            terminal_id=terminal_id,
            field_meta=metadata,
        )
        await self.client.kill_terminal(request)

    async def release_terminal(
        self,
        terminal_id: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Release a terminal session."""
        request = ReleaseTerminalRequest(
            session_id=self.id,
            terminal_id=terminal_id,
            field_meta=metadata,
        )
        await self.client.release_terminal(request)

    async def run_command(
        self,
        command: str,
        *,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        output_byte_limit: int | None = None,
        timeout_seconds: int | None = None,
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, int | None]:
        """Execute a shell command and return output and exit code.

        This is a high-level convenience method that creates a terminal,
        runs the command, waits for completion, and cleans up.

        Args:
            command: Command to execute
            args: Command arguments
            cwd: Working directory for command execution
            env: Environment variables for command execution
            output_byte_limit: Maximum bytes to capture from output
            timeout_seconds: Command timeout in seconds
            metadata: Metadata for the requests

        Returns:
            Tuple of (output, exit_code)
        """
        terminal_handle = await self.create_terminal(
            command=command,
            args=args,
            cwd=cwd,
            env=env,
            output_byte_limit=output_byte_limit,
            metadata=metadata,
        )
        terminal_id = terminal_handle.terminal_id

        try:
            if timeout_seconds:  # Wait for completion (with optional timeout)
                try:
                    coro = self.wait_for_terminal_exit(terminal_id, metadata=metadata)
                    exit_result = await asyncio.wait_for(coro, timeout=timeout_seconds)
                except TimeoutError:  # Kill on timeout and get partial output
                    await self.kill_terminal(terminal_id, metadata=metadata)
                    output_response = await self.terminal_output(terminal_id, metadata=metadata)
                    return output_response.output, None
            else:
                exit_result = await self.wait_for_terminal_exit(terminal_id, metadata=metadata)

            output_response = await self.terminal_output(terminal_id, metadata=metadata)
            return output_response.output, exit_result.exit_code

        finally:  # Always release terminal
            await self.release_terminal(terminal_id, metadata=metadata)

    async def elicit(self, request: ElicitationRequest) -> ElicitationResponse:
        """Ask the user for structured input (``elicitation/create``), scoped to this session.

        Only allowed if the client advertised the matching ``elicitation`` mode.
        """
        scoped = request.model_copy(update={"session_id": self.id, "request_id": None})
        return await self.client.elicitation(scoped)

    async def request_permission(
        self,
        tool_call_id: str,
        *,
        title: str | None = None,
        raw_input: dict[str, Any] | None = None,
        options: Sequence[PermissionOption] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> RequestPermissionResponse:
        """Request permission from user before executing a tool call.

        Args:
            tool_call_id: Unique identifier for the tool call
            title: Human-readable description of the operation
            raw_input: The raw input parameters for the tool call
            options: Available permission options (defaults to allow/reject once)
            metadata: Metadata to include with the request

        Returns:
            Permission response with user's decision
        """
        if options is None:
            options = [
                PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
                PermissionOption(option_id="reject-once", name="Reject", kind="reject_once"),
            ]

        tool_call = ToolCallUpdate(tool_call_id=tool_call_id, title=title, raw_input=raw_input)
        request = RequestPermissionRequest(
            session_id=self.id,
            tool_call=tool_call,
            options=options,
            field_meta=metadata,
        )
        return await self.client.request_permission(request)
