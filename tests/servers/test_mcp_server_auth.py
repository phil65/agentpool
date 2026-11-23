"""serve-mcp over HTTP lets in only clients that authenticate (``auth``)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastmcp.server.auth.providers.jwt import RSAKeyPair
import httpx
from pydantic import SecretStr, ValidationError
import pytest

from agentpool import AgentPool
from agentpool_config.pool_server import (
    MCPJWTAuthConfig,
    MCPPoolServerConfig,
    MCPTokenAuthConfig,
)
from agentpool_server.mcp_server.server import MCPServer


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool_config.pool_server import MCPAuthConfig


_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
_HEADERS = {"Accept": "application/json, text/event-stream"}
_ISSUER = "https://auth.example.com"
_AUDIENCE = "https://mcp.example.com/mcp"


@asynccontextmanager
async def _http(auth: MCPAuthConfig) -> AsyncIterator[httpx.AsyncClient]:
    async with AgentPool() as pool:
        config = MCPPoolServerConfig(transport="streamable-http", auth=auth)
        app = MCPServer(pool, config).fastmcp.http_app()
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://localhost") as http,
        ):
            yield http


async def _initialize(auth: MCPAuthConfig, token: str | None) -> httpx.Response:
    headers = _HEADERS | ({"Authorization": f"Bearer {token}"} if token else {})
    async with _http(auth) as http:
        return await http.post("/mcp", json=_INITIALIZE, headers=headers)


@pytest.mark.parametrize(
    ("token", "allowed"), [("zed-secret", True), ("guess", False), (None, False)]
)
async def test_clients_need_one_of_the_tokens(token: str | None, allowed: bool) -> None:
    auth = MCPTokenAuthConfig(tokens={"zed": SecretStr("zed-secret"), "ci": SecretStr("ci-secret")})
    response = await _initialize(auth, token)
    assert (response.status_code != 401) is allowed


def _jwt(keys: RSAKeyPair, **overrides: Any) -> MCPJWTAuthConfig:
    settings: dict[str, Any] = {
        "public_key": keys.public_key,
        "issuer": _ISSUER,
        "audience": _AUDIENCE,
        "required_scopes": ["mcp:agents"],
    }
    return MCPJWTAuthConfig.model_validate(settings | overrides)


@pytest.mark.parametrize(
    ("claims", "allowed"),
    [
        ({}, True),
        ({"audience": "https://other.example.com"}, False),  # A token for another server
        ({"issuer": "https://evil.example.com"}, False),
        ({"scopes": ["read"]}, False),  # Lacks the required scope
    ],
)
async def test_clients_need_a_jwt_for_this_server(claims: dict[str, Any], allowed: bool) -> None:
    keys = RSAKeyPair.generate()
    token = keys.create_token(
        **{"issuer": _ISSUER, "audience": _AUDIENCE, "scopes": ["mcp:agents"]} | claims
    )
    response = await _initialize(_jwt(keys), token)
    assert (response.status_code != 401) is allowed


async def test_the_authorization_server_is_advertised() -> None:
    keys = RSAKeyPair.generate()
    auth = _jwt(keys, authorization_servers=[_ISSUER], base_url="https://mcp.example.com")
    async with _http(auth) as http:
        unauthorized = await http.post("/mcp", json=_INITIALIZE, headers=_HEADERS)
        metadata = await http.get("/.well-known/oauth-protected-resource/mcp")
    assert unauthorized.status_code == 401
    assert "resource_metadata=" in unauthorized.headers["www-authenticate"]
    assert metadata.json()["authorization_servers"] == [f"{_ISSUER}/"]


def test_auth_is_refused_on_stdio() -> None:
    with pytest.raises(ValidationError, match="HTTP transports"):
        MCPPoolServerConfig(auth=MCPTokenAuthConfig(tokens={"zed": SecretStr("x")}))


async def test_auth_set_for_stdio_from_the_command_line_is_refused() -> None:
    http = MCPPoolServerConfig(
        transport="streamable-http",
        auth=MCPTokenAuthConfig(tokens={"zed": SecretStr("x")}),
    )
    async with AgentPool() as pool:
        with pytest.raises(ValueError, match="HTTP transports"):
            MCPServer(pool, http.model_copy(update={"transport": "stdio"}))


@pytest.mark.parametrize(
    "settings",
    [
        {},  # No key
        {"jwks_uri": "https://auth.example.com/jwks.json", "public_key": "k"},  # Both
        {"public_key": "k", "authorization_servers": [_ISSUER]},  # Without base_url
    ],
)
def test_jwt_settings_are_checked(settings: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MCPJWTAuthConfig.model_validate(settings)
