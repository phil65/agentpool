"""Typed OpenCode V2 API errors (``{"_tag": ..., "message": ...}`` bodies)."""

from typing import TYPE_CHECKING

from opencode_v2_sdk.models import (
    AgentNotFoundErrorEncoded,
    CommandNotFoundErrorEncoded,
    ConflictErrorEncoded,
    FileNotFoundErrorEncoded,
    ForbiddenErrorEncoded,
    FormAlreadySettledErrorEncoded,
    FormInvalidAnswerErrorEncoded,
    FormNotFoundErrorEncoded,
    InvalidCursorErrorEncoded,
    InvalidRequestErrorEncoded,
    LocationNotFoundErrorEncoded,
    LocationPublicRef,
    McpServerNotFoundErrorEncoded,
    MessageNotFoundErrorEncoded,
    ModelNotFoundErrorEncoded,
    PermissionNotFoundErrorEncoded,
    PermissionSavedNotFoundErrorEncoded,
    ProviderNotFoundErrorEncoded,
    PtyNotFoundErrorEncoded,
    ServiceUnavailableErrorEncoded,
    SessionBusyErrorEncoded,
    SessionNotFoundErrorEncoded,
    ShellNotFoundErrorEncoded,
    SkillNotFoundErrorEncoded,
    UnauthorizedErrorEncoded,
    UnknownErrorEncoded,
    UnsupportedModelErrorEncoded,
)


if TYPE_CHECKING:
    from pydantic import BaseModel


class ApiError(Exception):
    """An error answered with a typed OpenCode error body."""

    def __init__(self, status_code: int, body: BaseModel) -> None:
        """Initialize the error.

        Args:
            status_code: HTTP status code
            body: Encoded error model
        """
        super().__init__(str(body))
        self.status_code = status_code
        self.body = body


def invalid_request(message: str, *, field: str | None = None) -> ApiError:
    """400: malformed request."""
    return ApiError(400, InvalidRequestErrorEncoded(message=message, field=field))


def invalid_cursor(message: str = "Invalid cursor") -> ApiError:
    """400: malformed pagination cursor."""
    return ApiError(400, InvalidCursorErrorEncoded(message=message))


def unauthorized() -> ApiError:
    """401: missing or wrong credentials."""
    return ApiError(401, UnauthorizedErrorEncoded(message="Unauthorized"))


def forbidden(message: str) -> ApiError:
    """403: request refused (bad origin, missing ticket header, ...)."""
    return ApiError(403, ForbiddenErrorEncoded(message=message))


def session_not_found(session_id: str) -> ApiError:
    """404: unknown session."""
    message = f"Session not found: {session_id}"
    return ApiError(404, SessionNotFoundErrorEncoded(session_id=session_id, message=message))


def message_not_found(session_id: str, message_id: str) -> ApiError:
    """404: unknown message."""
    return ApiError(
        404,
        MessageNotFoundErrorEncoded(
            session_id=session_id, message_id=message_id, message=f"Message not found: {message_id}"
        ),
    )


def agent_not_found(agent_id: str) -> ApiError:
    """404: unknown agent."""
    message = f"Agent not found: {agent_id}"
    return ApiError(404, AgentNotFoundErrorEncoded(agent_id=agent_id, message=message))


def model_not_found(provider_id: str, model_id: str) -> ApiError:
    """404: unknown model."""
    return ApiError(
        404,
        ModelNotFoundErrorEncoded(
            provider_id=provider_id,
            model_id=model_id,
            message=f"Model not found: {provider_id}/{model_id}",
        ),
    )


def unsupported_model(provider_id: str, model_id: str, agent: str) -> ApiError:
    """400: the session's agent can't use this model."""
    return ApiError(
        400,
        UnsupportedModelErrorEncoded(
            provider_id=provider_id,
            model_id=model_id,
            capability="agent",
            message=f"Agent {agent!r} cannot use model {provider_id}/{model_id}",
        ),
    )


def provider_not_found(provider_id: str) -> ApiError:
    """404: unknown provider."""
    message = f"Provider not found: {provider_id}"
    return ApiError(404, ProviderNotFoundErrorEncoded(provider_id=provider_id, message=message))


def permission_not_found(request_id: str) -> ApiError:
    """404: unknown permission request."""
    message = f"Permission request not found: {request_id}"
    return ApiError(404, PermissionNotFoundErrorEncoded(request_id=request_id, message=message))


def saved_permission_not_found(rule_id: str) -> ApiError:
    """404: unknown saved permission."""
    message = f"Saved permission not found: {rule_id}"
    return ApiError(404, PermissionSavedNotFoundErrorEncoded(id=rule_id, message=message))


def form_not_found(form_id: str) -> ApiError:
    """404: unknown form."""
    return ApiError(404, FormNotFoundErrorEncoded(id=form_id, message=f"Form not found: {form_id}"))


def form_settled(form_id: str) -> ApiError:
    """409: form already answered or cancelled."""
    message = f"Form already settled: {form_id}"
    return ApiError(409, FormAlreadySettledErrorEncoded(id=form_id, message=message))


def form_invalid_answer(form_id: str, message: str) -> ApiError:
    """400: form answer doesn't match the form."""
    return ApiError(400, FormInvalidAnswerErrorEncoded(id=form_id, message=message))


def session_busy(session_id: str) -> ApiError:
    """409: the session is running and the operation needs it idle."""
    message = f"Session is busy: {session_id}"
    return ApiError(409, SessionBusyErrorEncoded(session_id=session_id, message=message))


def conflict(message: str, *, resource: str | None = None) -> ApiError:
    """409: conflicting state."""
    return ApiError(409, ConflictErrorEncoded(message=message, resource=resource))


def location_not_found(directory: str) -> ApiError:
    """404: the location directory does not exist."""
    return ApiError(
        404,
        LocationNotFoundErrorEncoded(
            location=LocationPublicRef(directory=directory),
            message=f"Location not found: {directory}",
        ),
    )


def file_not_found(path: str) -> ApiError:
    """404: unknown file."""
    return ApiError(404, FileNotFoundErrorEncoded(path=path, message=f"File not found: {path}"))


def skill_not_found(skill: str) -> ApiError:
    """404: unknown skill."""
    return ApiError(
        404, SkillNotFoundErrorEncoded(skill=skill, message=f"Skill not found: {skill}")
    )


def command_not_found(command: str) -> ApiError:
    """404: unknown command."""
    message = f"Command not found: {command}"
    return ApiError(404, CommandNotFoundErrorEncoded(command=command, message=message))


def mcp_server_not_found(server: str) -> ApiError:
    """404: unknown MCP server."""
    message = f"MCP server not found: {server}"
    return ApiError(404, McpServerNotFoundErrorEncoded(server=server, message=message))


def pty_not_found(pty_id: str) -> ApiError:
    """404: unknown PTY."""
    return ApiError(404, PtyNotFoundErrorEncoded(pty_id=pty_id, message=f"PTY not found: {pty_id}"))


def shell_not_found(shell_id: str) -> ApiError:
    """404: unknown shell command."""
    message = f"Shell not found: {shell_id}"
    return ApiError(404, ShellNotFoundErrorEncoded(id=shell_id, message=message))


def unavailable(service: str, message: str | None = None) -> ApiError:
    """503: a feature this server does not provide."""
    text = message or f"{service} is not supported by the AgentPool OpenCode server"
    return ApiError(503, ServiceUnavailableErrorEncoded(message=text, service=service))


def unknown(message: str) -> ApiError:
    """500: unexpected failure."""
    return ApiError(500, UnknownErrorEncoded(message=message))
