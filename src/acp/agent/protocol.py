"""Agent protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from acp.exceptions import RequestError


if TYPE_CHECKING:
    from acp.schema import (
        ArchiveSessionRequest,
        ArchiveSessionResponse,
        AuthenticateRequest,
        AuthenticateResponse,
        AuthStatusRequest,
        AuthStatusResponse,
        CancelNotification,
        CloseSessionRequest,
        CloseSessionResponse,
        DeleteSessionRequest,
        DeleteSessionResponse,
        DisableProviderRequest,
        DisableProviderResponse,
        ForkSessionRequest,
        ForkSessionResponse,
        InitializeRequest,
        InitializeResponse,
        ListProvidersRequest,
        ListProvidersResponse,
        ListSessionsRequest,
        ListSessionsResponse,
        LoadSessionRequest,
        LoadSessionResponse,
        LogoutRequest,
        LogoutResponse,
        MessageMcpNotification,
        MessageMcpRequest,
        MessageMcpResponse,
        NewSessionRequest,
        NewSessionResponse,
        PromptRequest,
        PromptResponse,
        ResumeSessionRequest,
        ResumeSessionResponse,
        SessionOptionsRequest,
        SessionOptionsResponse,
        SessionStatusRequest,
        SessionStatusResponse,
        SetProviderRequest,
        SetProviderResponse,
        SetSessionConfigOptionRequest,
        SetSessionConfigOptionResponse,
        SetSessionModeRequest,
        SetSessionModeResponse,
        SetSessionTitleRequest,
        SetSessionTitleResponse,
        UnarchiveSessionRequest,
        UnarchiveSessionResponse,
    )


@runtime_checkable
class Agent(Protocol):
    """Base agent interface for ACP.

    Methods with a default body are optional and gated by a capability the agent
    advertises. Their defaults reject the call with ``method not found``.
    """

    async def initialize(self, params: InitializeRequest) -> InitializeResponse: ...

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse: ...

    async def prompt(self, params: PromptRequest) -> PromptResponse: ...

    async def cancel(self, params: CancelNotification) -> None: ...

    async def load_session(self, params: LoadSessionRequest) -> LoadSessionResponse: ...

    async def list_sessions(self, params: ListSessionsRequest) -> ListSessionsResponse: ...

    async def fork_session(self, params: ForkSessionRequest) -> ForkSessionResponse: ...

    async def resume_session(self, params: ResumeSessionRequest) -> ResumeSessionResponse: ...

    async def close_session(self, params: CloseSessionRequest) -> CloseSessionResponse: ...

    async def authenticate(self, params: AuthenticateRequest) -> AuthenticateResponse | None: ...

    async def logout(self, params: LogoutRequest) -> LogoutResponse | None: ...

    async def set_session_mode(
        self, params: SetSessionModeRequest
    ) -> SetSessionModeResponse | None: ...

    async def set_session_config_option(
        self, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse | None: ...

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None: ...

    async def delete_session(self, params: DeleteSessionRequest) -> DeleteSessionResponse:
        raise RequestError.method_not_found("session/delete")

    async def archive_session(self, params: ArchiveSessionRequest) -> ArchiveSessionResponse:
        """**UNSTABLE**."""
        raise RequestError.method_not_found("session/archive")

    async def unarchive_session(self, params: UnarchiveSessionRequest) -> UnarchiveSessionResponse:
        """**UNSTABLE**."""
        raise RequestError.method_not_found("session/unarchive")

    async def session_status(self, params: SessionStatusRequest) -> SessionStatusResponse:
        """**UNSTABLE**."""
        raise RequestError.method_not_found("session/status")

    async def session_options(self, params: SessionOptionsRequest) -> SessionOptionsResponse:
        """**UNSTABLE**."""
        raise RequestError.method_not_found("session/options")

    async def set_session_title(self, params: SetSessionTitleRequest) -> SetSessionTitleResponse:
        """**UNSTABLE**."""
        raise RequestError.method_not_found("session/set_title")

    async def auth_status(self, params: AuthStatusRequest) -> AuthStatusResponse:
        """**UNSTABLE**."""
        raise RequestError.method_not_found("auth/status")

    async def list_providers(self, params: ListProvidersRequest) -> ListProvidersResponse:
        """**UNSTABLE**."""
        raise RequestError.method_not_found("providers/list")

    async def set_provider(self, params: SetProviderRequest) -> SetProviderResponse:
        """**UNSTABLE**."""
        raise RequestError.method_not_found("providers/set")

    async def disable_provider(self, params: DisableProviderRequest) -> DisableProviderResponse:
        """**UNSTABLE**."""
        raise RequestError.method_not_found("providers/disable")

    async def mcp_message(self, params: MessageMcpRequest) -> MessageMcpResponse:
        """**UNSTABLE**: Inner MCP request to an ``acp`` MCP server the agent declared."""
        raise RequestError.method_not_found("mcp/message")

    async def mcp_notification(self, params: MessageMcpNotification) -> None:
        """**UNSTABLE**: Inner MCP notification for an ``acp`` MCP server the agent declared."""
        return
