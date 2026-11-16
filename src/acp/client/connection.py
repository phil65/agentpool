"""Client ACP Connection."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, Self

import logfire

from acp.agent.protocol import Agent
from acp.client.protocol import Client
from acp.connection import Connection
from acp.exceptions import RequestError
from acp.schema import (
    AuthenticateResponse,
    CloseSessionResponse,
    CreateTerminalRequest,
    ForkSessionResponse,
    InitializeResponse,
    KillTerminalCommandRequest,
    ListSessionsResponse,
    LoadSessionResponse,
    LogoutResponse,
    NewSessionResponse,
    PromptResponse,
    ReadTextFileRequest,
    ReleaseTerminalRequest,
    RequestPermissionRequest,
    ResumeSessionResponse,
    SessionNotification,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    TerminalOutputRequest,
    WaitForTerminalExitRequest,
    WriteTextFileRequest,
)
from acp.schema.elicitation import (
    ElicitationCompleteNotification,
    ElicitationRequest,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from anyio.abc import ByteReceiveStream, ByteSendStream

    from acp.connection import StreamObserver
    from acp.schema import (
        AuthenticateRequest,
        CancelNotification,
        ClientMethod,
        ClientResponse,
        CloseSessionRequest,
        ForkSessionRequest,
        InitializeRequest,
        ListSessionsRequest,
        LoadSessionRequest,
        LogoutRequest,
        NewSessionRequest,
        PromptRequest,
        ResumeSessionRequest,
        SetSessionConfigOptionRequest,
        SetSessionModelRequest,
        SetSessionModeRequest,
    )


class ClientSideConnection(Agent):
    """Client-side connection.

    Use when you implement the Client and need to talk to an Agent.

    Args:
      to_client: factory that receives this connection and returns your Client
      input: ByteSendStream (local -> peer)
      output: ByteReceiveStream (peer -> local)
    """

    def __init__(
        self,
        to_client: Callable[[Agent], Client] | Client,
        input_stream: ByteSendStream,
        output_stream: ByteReceiveStream,
        observers: list[StreamObserver] | None = None,
    ) -> None:
        # Build client first so handler can delegate
        client = to_client if isinstance(to_client, Client) else to_client(self)
        handler = partial(_handle_client_method, client)
        self._conn = Connection(handler, input_stream, output_stream, observers=observers)

    # agent-bound methods (client -> agent)
    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        resp = await self._conn.send_request("initialize", dct)
        return InitializeResponse.model_validate(resp)

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        # Don't exclude_defaults - MCP server 'type' field is required for discriminated unions
        dct = params.model_dump(mode="json", by_alias=True, exclude_none=True)
        resp = await self._conn.send_request("session/new", dct)
        return NewSessionResponse.model_validate(resp)

    async def load_session(self, params: LoadSessionRequest) -> LoadSessionResponse:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        resp = await self._conn.send_request("session/load", dct)
        payload = resp if isinstance(resp, dict) else {}
        return LoadSessionResponse.model_validate(payload)

    async def list_sessions(self, params: ListSessionsRequest) -> ListSessionsResponse:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        resp = await self._conn.send_request("session/list", dct)
        return ListSessionsResponse.model_validate(resp)

    async def fork_session(self, params: ForkSessionRequest) -> ForkSessionResponse:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        resp = await self._conn.send_request("session/fork", dct)
        return ForkSessionResponse.model_validate(resp)

    async def resume_session(self, params: ResumeSessionRequest) -> ResumeSessionResponse:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        resp = await self._conn.send_request("session/resume", dct)
        payload = resp if isinstance(resp, dict) else {}
        return ResumeSessionResponse.model_validate(payload)

    async def close_session(self, params: CloseSessionRequest) -> CloseSessionResponse:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        resp = await self._conn.send_request("session/close", dct)
        payload = resp if isinstance(resp, dict) else {}
        return CloseSessionResponse.model_validate(payload)

    async def set_session_mode(self, params: SetSessionModeRequest) -> SetSessionModeResponse:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        resp = await self._conn.send_request("session/set_mode", dct)
        payload = resp if isinstance(resp, dict) else {}
        return SetSessionModeResponse.model_validate(payload)

    async def set_session_model(self, params: SetSessionModelRequest) -> SetSessionModelResponse:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        resp = await self._conn.send_request("session/set_model", dct)
        payload = resp if isinstance(resp, dict) else {}
        return SetSessionModelResponse.model_validate(payload)

    async def set_session_config_option(
        self, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        resp = await self._conn.send_request("session/set_config_option", dct)
        payload = resp if isinstance(resp, dict) else {}
        return SetSessionConfigOptionResponse.model_validate(payload)

    async def authenticate(self, params: AuthenticateRequest) -> AuthenticateResponse:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        resp = await self._conn.send_request("authenticate", dct)
        payload = resp if isinstance(resp, dict) else {}
        return AuthenticateResponse.model_validate(payload)

    async def logout(self, params: LogoutRequest) -> LogoutResponse:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        resp = await self._conn.send_request("logout", dct)
        payload = resp if isinstance(resp, dict) else {}
        return LogoutResponse.model_validate(payload)

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        # Don't exclude_defaults here - the 'type' field in content blocks has a default
        # value but is required for discriminated unions to work
        dct = params.model_dump(mode="json", by_alias=True, exclude_none=True)
        resp = await self._conn.send_request("session/prompt", dct)
        return PromptResponse.model_validate(resp)

    async def cancel(self, params: CancelNotification) -> None:
        dct = params.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
        await self._conn.send_notification("session/cancel", dct)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._conn.send_request(f"_{method}", params)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        await self._conn.send_notification(f"_{method}", params)

    async def close(self) -> None:
        await self._conn.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


@logfire.instrument(r"Handle Client Method {method}")
async def _handle_client_method(  # noqa: PLR0911
    client: Client,
    method: ClientMethod | str,
    params: dict[str, Any] | None,
    is_notification: bool,
) -> ClientResponse | dict[str, Any] | None:
    """Handle client method calls."""
    match method:
        case "fs/write_text_file":
            write_file_request = WriteTextFileRequest.model_validate(params)
            return await client.write_text_file(write_file_request)
        case "fs/read_text_file":
            read_file_request = ReadTextFileRequest.model_validate(params)
            return await client.read_text_file(read_file_request)
        case "session/request_permission":
            permission_request = RequestPermissionRequest.model_validate(params)
            return await client.request_permission(permission_request)
        case "session/update":
            notification = SessionNotification.model_validate(params)
            await client.session_update(notification)
            return None
        case "terminal/create":
            create_request = CreateTerminalRequest.model_validate(params)
            return await client.create_terminal(create_request)
        case "terminal/output":
            output_request = TerminalOutputRequest.model_validate(params)
            return await client.terminal_output(output_request)
        case "terminal/release":
            release_request = ReleaseTerminalRequest.model_validate(params)
            return await client.release_terminal(release_request)
        case "terminal/wait_for_exit":
            wait_request = WaitForTerminalExitRequest.model_validate(params)
            return await client.wait_for_terminal_exit(wait_request)
        case "terminal/kill":
            kill_request = KillTerminalCommandRequest.model_validate(params)
            return await client.kill_terminal(kill_request)
        case "session/elicitation":
            elicitation_request = ElicitationRequest.model_validate(params)
            return await client.elicitation(elicitation_request)
        case "session/elicitation/complete":
            complete_notification = ElicitationCompleteNotification.model_validate(params)
            await client.elicitation_complete(complete_notification)
            return None
        case str() if method.startswith("_") and is_notification:
            await client.ext_notification(method[1:], params or {})
            return None
        case str() if method.startswith("_") and is_notification:
            return await client.ext_method(method[1:], params or {})
        case _:
            raise RequestError.method_not_found(method)
