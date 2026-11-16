"""MCP schema definitions."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003
from typing import Literal

from pydantic import Field, HttpUrl

from acp.schema.base import AnnotatedObject, Schema
from acp.schema.common import EnvVariable  # noqa: TC001


class HttpHeader(AnnotatedObject):
    """An HTTP header to set when making requests to the MCP server."""

    name: str
    """The name of the HTTP header."""

    value: str
    """The value to set for the HTTP header."""


class BaseMcpServer(Schema):
    """MCP server base class."""

    name: str
    """Human-readable name identifying this MCP server."""


class HttpMcpServer(BaseMcpServer):
    """HTTP transport configuration.

    Only available when the Agent capabilities indicate `mcp_capabilities.http` is `true`.
    """

    headers: Sequence[HttpHeader] = Field(default_factory=list)
    """HTTP headers to set when making requests to the MCP server."""

    type: Literal["http"] = Field(default="http", init=False)
    """HTTP transport type."""

    url: HttpUrl
    """URL to the MCP server."""

    def get_headers_dict(self) -> dict[str, str]:
        return {header.name: header.value for header in self.headers}


class SseMcpServer(BaseMcpServer):
    """SSE transport configuration.

    Only available when the Agent capabilities indicate `mcp_capabilities.sse` is `true`.
    """

    headers: Sequence[HttpHeader]
    """HTTP headers to set when making requests to the MCP server."""

    type: Literal["sse"] = Field(default="sse", init=False)
    """SSE transport type."""

    url: HttpUrl
    """URL to the MCP server."""

    def get_headers_dict(self) -> dict[str, str]:
        return {header.name: header.value for header in self.headers}


class StdioMcpServer(BaseMcpServer):
    """Stdio transport configuration.

    All Agents MUST support this transport.
    """

    args: Sequence[str]
    """Command-line arguments to pass to the MCP server."""

    # typ: Literal["stdio"] = Field(default="stdio", init=False)
    # """Stdio transport type."""

    command: str
    """Path to the MCP server executable."""

    env: Sequence[EnvVariable]
    """Environment variables to set when launching the MCP server."""

    def get_env_dict(self) -> dict[str, str]:
        return {env.name: env.value for env in self.env}


McpServer = HttpMcpServer | SseMcpServer | StdioMcpServer
