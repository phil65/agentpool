from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from acp.schema import (
        CreateTerminalRequest,
        CreateTerminalResponse,
        KillTerminalCommandRequest,
        KillTerminalCommandResponse,
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


@runtime_checkable
class Client(Protocol):
    """Base client interface for ACP."""

    async def request_permission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse: ...

    async def session_update(self, params: SessionNotification) -> None: ...

    async def write_text_file(
        self, params: WriteTextFileRequest
    ) -> WriteTextFileResponse | None: ...

    async def read_text_file(self, params: ReadTextFileRequest) -> ReadTextFileResponse: ...

    async def create_terminal(self, params: CreateTerminalRequest) -> CreateTerminalResponse: ...

    async def terminal_output(self, params: TerminalOutputRequest) -> TerminalOutputResponse: ...

    async def release_terminal(
        self, params: ReleaseTerminalRequest
    ) -> ReleaseTerminalResponse | None: ...

    async def wait_for_terminal_exit(
        self, params: WaitForTerminalExitRequest
    ) -> WaitForTerminalExitResponse: ...

    async def kill_terminal(
        self, params: KillTerminalCommandRequest
    ) -> KillTerminalCommandResponse | None: ...

    async def elicitation(self, params: ElicitationRequest) -> ElicitationResponse: ...

    async def elicitation_complete(self, params: ElicitationCompleteNotification) -> None: ...

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None: ...
