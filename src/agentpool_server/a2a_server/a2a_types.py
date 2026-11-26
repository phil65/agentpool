"""A2A types."""

from typing import Any, TypedDict


class A2ARequest(TypedDict):
    """A2A JSON-RPC request format."""

    jsonrpc: str
    method: str
    params: dict[str, Any]
    id: str | int | None


class A2AResponse(TypedDict):
    """A2A JSON-RPC response format."""

    jsonrpc: str
    id: str | int | None
    result: dict[str, Any]


class TaskData(TypedDict):
    """Task storage data format."""

    id: str
    status: str
    data: A2ARequest
    result: dict[str, Any] | None
    error: str | None
    context_id: str | None


class TaskStatus(TypedDict):
    """Task status response format."""

    status: str
    result: dict[str, Any] | None
    error: str | None
