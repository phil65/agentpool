"""MCP OAuth honours ``auth.persist`` (keyring token storage instead of memory)."""

from typing import TYPE_CHECKING

from fastmcp.client.auth import OAuth
from key_value.aio.stores.keyring import KeyringStore
import keyring
from keyring.backend import KeyringBackend
from keyring.backends.fail import Keyring as FailKeyring
import pytest

from agentpool.mcp_server.client import MCPClient
from agentpool_config.mcp_server import (
    KEYRING_SERVICE_NAME,
    MCPServerAuthSettings,
    SSEMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)


if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeKeyring(KeyringBackend):
    """In-memory keyring backend, so no OS keychain is touched."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.passwords.pop((service, username), None)


@pytest.fixture
def fake_keyring() -> Iterator[FakeKeyring]:
    previous = keyring.get_keyring()
    backend = FakeKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)


def _oauth_of(client: MCPClient) -> OAuth:
    auth = client._client.transport.auth  # type: ignore[attr-defined]
    assert isinstance(auth, OAuth)
    return auth


@pytest.mark.parametrize("config_cls", [StreamableHTTPMCPServerConfig, SSEMCPServerConfig])
async def test_keyring_persist_uses_keyring_store(
    fake_keyring: FakeKeyring,
    config_cls: type[StreamableHTTPMCPServerConfig | SSEMCPServerConfig],
) -> None:
    auth = MCPServerAuthSettings(oauth=True, persist="keyring", scope=["read", "write"])
    config = config_cls(url="https://example.com/mcp", auth=auth)  # type: ignore[arg-type]
    oauth = _oauth_of(MCPClient(config))
    store = oauth._token_storage
    assert isinstance(store, KeyringStore)
    assert oauth._scopes == ["read", "write"]
    # Tokens written through the store end up in the (fake) OS keyring
    await store.put(key="https://example.com/mcp/tokens", value={"access_token": "abc"})
    assert [service for service, _ in fake_keyring.passwords] == [KEYRING_SERVICE_NAME]
    assert await store.get(key="https://example.com/mcp/tokens") == {"access_token": "abc"}


def test_memory_persist_keeps_fastmcp_default(fake_keyring: FakeKeyring) -> None:
    auth = MCPServerAuthSettings(oauth=True, persist="memory")
    config = StreamableHTTPMCPServerConfig(url="https://example.com/mcp", auth=auth)  # type: ignore[arg-type]
    assert _oauth_of(MCPClient(config))._token_storage is None


def test_no_oauth_means_no_auth() -> None:
    config = StreamableHTTPMCPServerConfig(url="https://example.com/mcp")  # type: ignore[arg-type]
    assert MCPClient(config)._client.transport.auth is None  # type: ignore[attr-defined]


def test_keyring_without_backend_falls_back_to_memory() -> None:
    previous = keyring.get_keyring()
    keyring.set_keyring(FailKeyring())
    try:
        assert MCPServerAuthSettings(persist="keyring").get_token_storage() is None
    finally:
        keyring.set_keyring(previous)


@pytest.mark.parametrize("config_cls", [StreamableHTTPMCPServerConfig, SSEMCPServerConfig])
def test_to_pydantic_ai_passes_oauth(
    fake_keyring: FakeKeyring,
    config_cls: type[StreamableHTTPMCPServerConfig | SSEMCPServerConfig],
) -> None:
    auth = MCPServerAuthSettings(oauth=True, persist="keyring")
    config = config_cls(url="https://example.com/mcp", auth=auth)  # type: ignore[arg-type]
    toolset = config.to_pydantic_ai()
    oauth = toolset.client.transport.auth  # type: ignore[attr-defined]
    assert isinstance(oauth, OAuth)
    assert isinstance(oauth._token_storage, KeyringStore)
