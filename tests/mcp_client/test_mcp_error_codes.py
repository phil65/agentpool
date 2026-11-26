"""An MCP error the model can act on is retried; transport and server faults end the run."""

from __future__ import annotations

import sys
from typing import Any

from mcp import MCPError
from mcp_types import (
    CONNECTION_CLOSED,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    REQUEST_TIMEOUT,
)
from pydantic_ai import ModelRetry
import pytest

from agentpool.mcp_server.client import MCPClient
from agentpool_config.mcp_server import StdioMCPServerConfig


class _FailingClient:
    def __init__(self, code: int) -> None:
        self.code = code

    def is_connected(self) -> bool:
        return True

    async def call_tool(self, *args: Any, **kwargs: Any) -> Any:
        raise MCPError(code=self.code, message=f"error {self.code}")


def _client(code: int) -> MCPClient:
    client = MCPClient(StdioMCPServerConfig(name="s", command=sys.executable, args=[]))
    client._client = _FailingClient(code)  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
    return client


@pytest.mark.parametrize("code", [INVALID_PARAMS, -1, 42])
async def test_a_refused_call_is_retried(code: int) -> None:
    with pytest.raises(ModelRetry):
        await _client(code).call_tool("t", None)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "code",
    [
        CONNECTION_CLOSED,
        REQUEST_TIMEOUT,
        INTERNAL_ERROR,
        PARSE_ERROR,
        INVALID_REQUEST,
        METHOD_NOT_FOUND,
    ],
)
async def test_transport_and_server_faults_end_the_run(code: int) -> None:
    with pytest.raises(RuntimeError, match=f"error {code}"):
        await _client(code).call_tool("t", None)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
