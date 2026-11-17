"""No-op ACP client implementation for bridging scenarios.

This module provides a minimal client that returns empty/default responses
for all operations. Useful for scenarios where you need a Client implementation
but don't actually need to handle client-side operations (e.g., when bridging
an agent to a different transport).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from acp.client.protocol import Client


if TYPE_CHECKING:
    from acp.schema import (
        CreateTerminalRequest,
        CreateTerminalResponse,
        KillTerminalRequest,
        KillTerminalResponse,
        ReadTextFileRequest,
        ReadTextFileResponse,
        ReleaseTerminalRequest,
        ReleaseTerminalResponse,
        RequestPermissionRequest,
        RequestPermissionResponse,
        SessionNotification,
        TerminalOutputRequest,
        TerminalOutputResponse,
        WaitForTerminalExitRequest,
        WaitForTerminalExitResponse,
        WriteTextFileRequest,
        WriteTextFileResponse,
    )
    from acp.schema.elicitation import (
        ElicitationCompleteNotification,
        ElicitationRequest,
        ElicitationResponse,
    )


class NoOpClient(Client):
    """Minimal client that returns default responses for all operations.

    This client is useful for bridging scenarios where you need a Client
    implementation but don't actually need to handle client-side operations.
    All methods return minimal valid responses.
    """

    async def request_permission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse:
        """Grant permission using first available option."""
        from acp.schema import AllowedOutcome, RequestPermissionResponse

        return RequestPermissionResponse(outcome=AllowedOutcome(option_id="allow"))

    async def session_update(self, params: SessionNotification) -> None:
        """Ignore session updates."""

    async def write_text_file(self, params: WriteTextFileRequest) -> WriteTextFileResponse:
        """Return empty write response."""
        from acp.schema import WriteTextFileResponse

        return WriteTextFileResponse()

    async def read_text_file(self, params: ReadTextFileRequest) -> ReadTextFileResponse:
        """Return empty file content."""
        from acp.schema import ReadTextFileResponse

        return ReadTextFileResponse(content="")

    async def create_terminal(self, params: CreateTerminalRequest) -> CreateTerminalResponse:
        """Return dummy terminal ID."""
        from acp.schema import CreateTerminalResponse

        return CreateTerminalResponse(terminal_id="noop-terminal")

    async def terminal_output(self, params: TerminalOutputRequest) -> TerminalOutputResponse:
        """Return empty terminal output."""
        from acp.schema import TerminalOutputResponse

        return TerminalOutputResponse(output="", truncated=False)

    async def release_terminal(
        self, params: ReleaseTerminalRequest
    ) -> ReleaseTerminalResponse | None:
        """Return empty release response."""
        from acp.schema import ReleaseTerminalResponse

        return ReleaseTerminalResponse()

    async def wait_for_terminal_exit(
        self, params: WaitForTerminalExitRequest
    ) -> WaitForTerminalExitResponse:
        """Return immediate exit with code 0."""
        from acp.schema import WaitForTerminalExitResponse

        return WaitForTerminalExitResponse(exit_code=0)

    async def kill_terminal(self, params: KillTerminalRequest) -> KillTerminalResponse | None:
        """Return empty kill response."""
        from acp.schema import KillTerminalResponse

        return KillTerminalResponse()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Return empty dict for extension methods."""
        return {}

    async def elicitation(self, params: ElicitationRequest) -> ElicitationResponse:
        """Decline elicitation by default."""
        from acp.schema.elicitation import ElicitationResponse

        return ElicitationResponse.decline()

    async def elicitation_complete(self, params: ElicitationCompleteNotification) -> None:
        """Ignore elicitation complete notifications."""

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Ignore extension notifications."""
