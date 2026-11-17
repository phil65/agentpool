"""Agent ACP Connection."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, Self

import logfire
import structlog

from acp.agent.protocol import Agent
from acp.client.protocol import Client
from acp.connection import Connection
from acp.exceptions import RequestError
from acp.schema import (
    ArchiveSessionRequest,
    AuthenticateRequest,
    AuthStatusRequest,
    CancelNotification,
    CloseSessionRequest,
    CreateTerminalRequest,
    CreateTerminalResponse,
    DeleteSessionRequest,
    DisableProviderRequest,
    ForkSessionRequest,
    InitializeRequest,
    KillTerminalRequest,
    KillTerminalResponse,
    ListProvidersRequest,
    ListSessionsRequest,
    LoadSessionRequest,
    LogoutRequest,
    MessageMcpNotification,
    MessageMcpRequest,
    MessageMcpResponse,
    NewSessionRequest,
    PromptRequest,
    ReadTextFileRequest,
    ReadTextFileResponse,
    ReleaseTerminalRequest,
    ReleaseTerminalResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    ResumeSessionRequest,
    SessionNotification,
    SessionOptionsRequest,
    SessionStatusRequest,
    SetProviderRequest,
    SetSessionConfigOptionRequest,
    SetSessionModeRequest,
    SetSessionTitleRequest,
    TerminalOutputRequest,
    TerminalOutputResponse,
    UnarchiveSessionRequest,
    WaitForTerminalExitRequest,
    WaitForTerminalExitResponse,
    WriteTextFileRequest,
    WriteTextFileResponse,
)
from acp.schema.elicitation import (
    ElicitationResponse,
)
from acp.task import DebuggingMessageStateStore


if TYPE_CHECKING:
    from collections.abc import Callable

    from anyio.abc import ByteReceiveStream, ByteSendStream

    from acp.connection import StreamObserver
    from acp.schema import (
        AgentMethod,
        AgentResponse,
        AuthStatusUpdateNotification,
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
    from acp.schema.elicitation import ElicitationCompleteNotification, ElicitationRequest

log = structlog.get_logger(__name__)


class AgentSideConnection(Client):
    """Agent-side connection.

    Use when you implement the Agent and need to talk to a Client.

    Args:
        to_agent: factory that receives this connection and returns your Agent
        input: ByteSendStream (local -> peer)
        output: ByteReceiveStream (peer -> local)
    """

    def __init__(
        self,
        to_agent: Callable[[AgentSideConnection], Agent] | Agent,
        input_stream: ByteSendStream,
        output_stream: ByteReceiveStream,
        observers: list[StreamObserver] | None = None,
        *,
        debug_file: str | None = None,
    ) -> None:
        """Initialize the agent-side connection.

        Args:
            to_agent: factory that receives this connection and returns your Agent
            input_stream: ByteSendStream (local -> peer)
            output_stream: ByteReceiveStream (peer -> local)
            observers: list of StreamObserver instances to observe the connection
            debug_file: path to a file to write debug information to
        """
        agent = to_agent if isinstance(to_agent, Agent) else to_agent(self)
        handler = partial(_agent_handler, agent)
        store = DebuggingMessageStateStore(debug_file=debug_file) if debug_file else None
        self._conn = Connection(
            handler,
            input_stream,
            output_stream,
            state_store=store,
            observers=observers,
        )

    # client-bound methods (agent -> client)
    async def session_update(self, params: SessionNotification) -> None:
        """Send session update notification."""
        try:
            dct = params.model_dump(by_alias=True, exclude_none=True)
        except TypeError as e:
            log.exception(
                "Failed to serialize SessionNotification",
                error=str(e),
                params_type=type(params).__name__,
                update_type=type(getattr(params, "update", None)).__name__,
                update_repr=repr(getattr(params, "update", None))[:500],
            )
            raise
        await self._conn.send_notification("session/update", dct)

    async def request_permission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse:
        """Request permission from the client."""
        dct = params.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        method = "session/request_permission"
        resp = await self._conn.send_request(method, dct)
        return RequestPermissionResponse.model_validate(resp)

    async def read_text_file(self, params: ReadTextFileRequest) -> ReadTextFileResponse:
        """Read text file from the client."""
        dct = params.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        resp = await self._conn.send_request("fs/read_text_file", dct)
        return ReadTextFileResponse.model_validate(resp)

    async def write_text_file(self, params: WriteTextFileRequest) -> WriteTextFileResponse:
        """Write text file to the client."""
        dct = params.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        r = await self._conn.send_request("fs/write_text_file", dct)
        return WriteTextFileResponse.model_validate(r)

    # async def createTerminal(self, params: CreateTerminalRequest) -> TerminalHandle:
    async def create_terminal(self, params: CreateTerminalRequest) -> CreateTerminalResponse:
        """Create a terminal on the client."""
        dct = params.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        resp = await self._conn.send_request("terminal/create", dct)
        #  resp = CreateTerminalResponse.model_validate(resp)
        #  return TerminalHandle(resp.terminal_id, params.session_id, self._conn)
        return CreateTerminalResponse.model_validate(resp)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Call an extension method on the client."""
        return await self._conn.send_request(f"_{method}", params)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send an extension notification to the client."""
        await self._conn.send_notification(f"_{method}", params)

    async def terminal_output(self, params: TerminalOutputRequest) -> TerminalOutputResponse:
        """Show terminal output on the client."""
        dct = params.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        resp = await self._conn.send_request("terminal/output", dct)
        return TerminalOutputResponse.model_validate(resp)

    async def release_terminal(self, params: ReleaseTerminalRequest) -> ReleaseTerminalResponse:
        """Release a terminal on the client."""
        dct = params.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        resp = await self._conn.send_request("terminal/release", dct)
        return ReleaseTerminalResponse.model_validate(resp)

    async def wait_for_terminal_exit(
        self, params: WaitForTerminalExitRequest
    ) -> WaitForTerminalExitResponse:
        """Wait for a terminal to exit on the client."""
        dct = params.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        resp = await self._conn.send_request("terminal/wait_for_exit", dct)
        return WaitForTerminalExitResponse.model_validate(resp)

    async def kill_terminal(self, params: KillTerminalRequest) -> KillTerminalResponse:
        """Kill a terminal on the client."""
        dct = params.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        resp = await self._conn.send_request("terminal/kill", dct)
        return KillTerminalResponse.model_validate(resp)

    async def elicitation(self, params: ElicitationRequest) -> ElicitationResponse:
        """Request structured user input from the client."""
        dct = params.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        resp = await self._conn.send_request("elicitation/create", dct)
        return ElicitationResponse.model_validate(resp)

    async def elicitation_complete(self, params: ElicitationCompleteNotification) -> None:
        """Notify the client that a URL-based elicitation has completed."""
        dct = params.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        await self._conn.send_notification("elicitation/complete", dct)

    async def auth_status_update(self, params: AuthStatusUpdateNotification) -> None:
        """**UNSTABLE**: Notify the client that the authentication state changed."""
        dct = params.model_dump(by_alias=True, exclude_none=True)
        await self._conn.send_notification("auth/status_update", dct)

    async def mcp_message(self, params: MessageMcpRequest) -> MessageMcpResponse:
        """**UNSTABLE**: Send an inner MCP request to an ``acp`` MCP server the client declared."""
        dct = params.model_dump(by_alias=True, exclude_none=True)
        resp = await self._conn.send_request("mcp/message", dct)
        return MessageMcpResponse.model_validate(resp)

    async def mcp_notification(self, params: MessageMcpNotification) -> None:
        """**UNSTABLE**: Send an inner MCP notification over ``mcp/message``."""
        dct = params.model_dump(by_alias=True, exclude_none=True)
        await self._conn.send_notification("mcp/message", dct)

    async def close(self) -> None:
        """Close the connection."""
        await self._conn.close()

    async def __aenter__(self) -> Self:
        """Enter the async context."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit the async context (close the connection)."""
        await self.close()


@logfire.instrument(r"Handle Agent Request {method}")
async def _agent_handler(  # noqa: PLR0911
    agent: Agent,
    method: AgentMethod | str,
    params: dict[str, Any] | None,
    is_notification: bool,
) -> AgentResponse | dict[str, Any] | None:
    """Handle an agent request."""
    match method:
        case "mcp/message" if is_notification:
            await agent.mcp_notification(MessageMcpNotification.model_validate(params))
            return None
        case "mcp/message":
            return await agent.mcp_message(MessageMcpRequest.model_validate(params))
        case "session/fork":
            return await agent.fork_session(ForkSessionRequest.model_validate(params))
        case "session/resume":
            return await agent.resume_session(ResumeSessionRequest.model_validate(params))
        case "session/delete":
            return await agent.delete_session(DeleteSessionRequest.model_validate(params))
        case "session/archive":
            return await agent.archive_session(ArchiveSessionRequest.model_validate(params))
        case "session/unarchive":
            return await agent.unarchive_session(UnarchiveSessionRequest.model_validate(params))
        case "session/status":
            return await agent.session_status(SessionStatusRequest.model_validate(params))
        case "session/options":
            return await agent.session_options(SessionOptionsRequest.model_validate(params))
        case "session/set_title":
            return await agent.set_session_title(SetSessionTitleRequest.model_validate(params))
        case "auth/status":
            return await agent.auth_status(AuthStatusRequest.model_validate(params or {}))
        case "providers/list":
            return await agent.list_providers(ListProvidersRequest.model_validate(params or {}))
        case "providers/set":
            return await agent.set_provider(SetProviderRequest.model_validate(params))
        case "providers/disable":
            return await agent.disable_provider(DisableProviderRequest.model_validate(params))
        case "initialize":
            initialize_request = InitializeRequest.model_validate(params)
            return await agent.initialize(initialize_request)
        case "session/new":
            new_session_request = NewSessionRequest.model_validate(params)
            return await agent.new_session(new_session_request)
        case "session/load":
            load_request = LoadSessionRequest.model_validate(params)
            return await agent.load_session(load_request)
        case "session/list":
            list_request = ListSessionsRequest.model_validate(params)
            return await agent.list_sessions(list_request)
        case "session/set_mode":
            set_mode_request = SetSessionModeRequest.model_validate(params)
            return await agent.set_session_mode(set_mode_request)
        case "session/prompt":
            prompt_request = PromptRequest.model_validate(params)
            return await agent.prompt(prompt_request)
        case "session/cancel":
            cancel_notification = CancelNotification.model_validate(params)
            await agent.cancel(cancel_notification)
            return None

        case "session/close":
            stop_request = CloseSessionRequest.model_validate(params)
            return await agent.close_session(stop_request)
        case "session/set_config_option":
            set_config_request = SetSessionConfigOptionRequest.model_validate(params)
            return await agent.set_session_config_option(set_config_request)
        case "authenticate":
            auth_request = AuthenticateRequest.model_validate(params)
            return await agent.authenticate(auth_request)
        case "logout":
            logout_request = LogoutRequest.model_validate(params)
            return await agent.logout(logout_request)
        case str() if method.startswith("_") and is_notification:
            await agent.ext_notification(method[1:], params or {})
            return None
        case str() if method.startswith("_"):
            return await agent.ext_method(method[1:], params or {})
        case _ as unknown_method:
            raise RequestError.method_not_found(unknown_method)
