"""Test fixtures for ACP tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from acp import Agent
from acp.schema import (
    AuthenticateResponse,
    CloseSessionResponse,
    ForkSessionResponse,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    LogoutResponse,
    NewSessionResponse,
    PromptResponse,
    ResumeSessionResponse,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
)


if TYPE_CHECKING:
    from acp import SetSessionModeRequest
    from acp.schema import (
        AuthenticateRequest,
        CancelNotification,
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
    )


class TestAgent(Agent):
    """Test agent implementation for ACP testing."""

    def __init__(self) -> None:
        self.prompts: list[PromptRequest] = []
        self.cancellations: list[str] = []
        self.ext_calls: list[tuple[str, dict[str, Any]]] = []
        self.ext_notes: list[tuple[str, dict[str, Any]]] = []

    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=params.protocol_version,
            agent_capabilities=None,
            agent_info=Implementation(name="test-agent", version="1.0"),
        )

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        return NewSessionResponse(session_id="test-session-123")

    async def load_session(self, params: LoadSessionRequest) -> LoadSessionResponse:
        return LoadSessionResponse()

    async def authenticate(self, params: AuthenticateRequest) -> AuthenticateResponse | None:
        return AuthenticateResponse()

    async def logout(self, params: LogoutRequest) -> LogoutResponse | None:
        return LogoutResponse()

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        self.prompts.append(params)
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, params: CancelNotification) -> None:
        self.cancellations.append(params.session_id)

    async def set_session_mode(self, params: SetSessionModeRequest) -> SetSessionModeResponse:
        return SetSessionModeResponse()

    async def set_session_model(self, params: SetSessionModelRequest) -> SetSessionModelResponse:
        return SetSessionModelResponse()

    async def set_session_config_option(
        self, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse:
        """Mock session config option change."""
        return SetSessionConfigOptionResponse()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.ext_calls.append((method, params))
        return {"ok": True, "method": method}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        self.ext_notes.append((method, params))

    async def list_sessions(self, params: ListSessionsRequest) -> ListSessionsResponse:
        return ListSessionsResponse(sessions=[])

    async def fork_session(self, params: ForkSessionRequest) -> ForkSessionResponse:
        return ForkSessionResponse(session_id="test-session-123")

    async def resume_session(self, params: ResumeSessionRequest) -> ResumeSessionResponse:
        return ResumeSessionResponse()

    async def close_session(self, params: CloseSessionRequest) -> CloseSessionResponse:
        return CloseSessionResponse()
