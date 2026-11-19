"""ACP-related exceptions."""

from __future__ import annotations

from typing import Any, Self


class RequestError(Exception):
    """Raised when a JSON-RPC request fails."""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data

    @classmethod
    def parse_error(cls, data: dict[str, Any] | None = None) -> Self:
        return cls(-32700, "Parse error", data)

    @classmethod
    def invalid_request(cls, data: dict[str, Any] | None = None) -> Self:
        return cls(-32600, "Invalid request", data)

    @classmethod
    def method_not_found(cls, method: str) -> Self:
        return cls(-32601, "Method not found", {"method": method})

    @classmethod
    def invalid_params(cls, data: dict[str, Any] | None = None) -> Self:
        return cls(-32602, "Invalid params", data)

    @classmethod
    def internal_error(cls, data: dict[str, Any] | None = None) -> Self:
        return cls(-32603, "Internal error", data)

    @classmethod
    def request_cancelled(cls, data: dict[str, Any] | None = None) -> Self:
        return cls(-32800, "Request cancelled", data)

    @classmethod
    def resource_not_found(cls, uri: str | None = None) -> Self:
        data = {"uri": uri} if uri is not None else None
        return cls(-32002, "Resource not found", data)

    @classmethod
    def auth_required(cls, data: dict[str, Any] | None = None) -> Self:
        return cls(-32000, "Authentication required", data)

    @classmethod
    def session_not_found(cls, session_id: str) -> Self:
        """``-32002`` for a request naming a session that does not exist or is not active."""
        return cls(-32002, "Resource not found", {"sessionId": session_id})

    @classmethod
    def unsupported_protocol_version(cls, requested: int, supported: list[int]) -> Self:
        """``-32022``: no supported protocol version is at or below the requested one."""
        data = {"requested": requested, "supported": supported}
        return cls(-32022, "Unsupported protocol version", data)

    @classmethod
    def from_error_obj(cls, error: Any) -> Self:
        """Build an error from a peer's JSON-RPC error object.

        Peers are not trusted to send a well-formed object: ``data`` is optional per
        JSON-RPC 2.0 and ``code`` / ``message`` may be missing or have the wrong type.
        """
        if not isinstance(error, dict):
            return cls.internal_error({"details": "Malformed error response", "error": error})
        code = error.get("code")
        message = error.get("message")
        return cls(
            code if isinstance(code, int) and not isinstance(code, bool) else -32603,
            message if isinstance(message, str) and message else "Error",
            error.get("data"),
        )

    def to_error_obj(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "data": self.data}
