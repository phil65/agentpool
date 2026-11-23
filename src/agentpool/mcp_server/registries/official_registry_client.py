"""MCP Registry client service for discovering and managing MCP servers.

This module provides functionality to interact with the Model Context Protocol
registry API for server discovery and configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any, Literal, Self

import anyio
import httpx2
from pydantic import Field
from schemez import Schema


# Constants
HTTP_NOT_FOUND = 404
CACHE_TTL = 3600  # 1 hour
NAME = "io.modelcontextprotocol.registry/official"

ServiceName = str
log = logging.getLogger(__name__)
TransportType = Literal["stdio", "sse", "websocket", "http"]


class UnsupportedTransportError(Exception):
    """Raised when no supported transport is available."""


class RegistryRepository(Schema):
    """Repository information for a registry server."""

    url: str | None = None
    source: str | None = None
    """Repository platform (e.g., 'github')."""

    id: str | None = None
    """Repository identifier."""

    subfolder: str | None = None
    """Repository subfolder path."""


class RegistryTransport(Schema):
    """Transport configuration for a package."""

    type: str
    """Transport type (stdio, sse, streamable-http)."""

    url: str | None = None
    """URL for HTTP transports."""

    headers: list[dict[str, Any]] = Field(default_factory=list)
    """Request headers."""


class RegistryPackage(Schema):
    """Package information for installing an MCP server."""

    registry_type: str = Field(alias="registryType")
    """Package registry type (npm, pypi, docker)."""

    identifier: str
    """Package identifier."""

    version: str | None = None
    """Package version."""

    transport: RegistryTransport
    """Transport configuration."""

    environment_variables: list[dict[str, Any]] = Field(
        default_factory=list, alias="environmentVariables"
    )
    """Environment variables."""

    package_arguments: list[dict[str, Any]] = Field(default_factory=list, alias="packageArguments")
    """Package arguments."""

    runtime_arguments: list[dict[str, Any]] = Field(default_factory=list, alias="runtimeArguments")
    """Runtime arguments."""

    runtime_hint: str | None = Field(None, alias="runtimeHint")
    """Runtime hint."""

    registry_base_url: str | None = Field(None, alias="registryBaseUrl")
    """Registry base URL."""

    file_sha256: str | None = Field(None, alias="fileSha256")
    """File SHA256 hash."""


class RegistryRemote(Schema):
    """Remote endpoint configuration."""

    type: str
    """Remote type (sse, streamable-http)."""

    url: str
    """Remote URL."""

    headers: list[dict[str, Any]] = Field(default_factory=list)
    """Request headers."""

    variables: dict[str, Any] = Field(default_factory=dict)
    """URL template variables."""


class RegistryIcon(Schema):
    """Icon configuration for a server."""

    src: str
    """Icon source URL."""

    theme: str | None = None
    """Theme variant (light, dark)."""

    mime_type: str | None = Field(None, alias="mimeType")
    """MIME type of the icon (e.g., image/png)."""

    sizes: list[str] = Field(default_factory=list)
    """Icon size descriptors (e.g., '200x200')."""


class RegistryServer(Schema):
    """MCP server entry from the registry."""

    name: ServiceName
    """Unique server identifier."""

    description: str
    """Server description."""

    version: str
    """Server version."""

    repository: RegistryRepository | None = None
    """Repository information."""

    packages: list[RegistryPackage] = Field(default_factory=list)
    """Available packages."""

    remotes: list[RegistryRemote] = Field(default_factory=list)
    """Remote endpoints."""

    schema_: str | None = Field(None, alias="$schema")
    """JSON schema URL."""

    title: str | None = None
    """Human-readable display title."""

    website_url: str | None = Field(None, alias="websiteUrl")
    """Website URL for documentation."""

    icons: list[RegistryIcon] = Field(default_factory=list)
    """Server icons for different themes."""

    meta: dict[str, Any] = Field(default_factory=dict, alias="_meta")
    """Internal metadata (can appear at server level too)."""

    def get_preferred_transport(self) -> TransportType:
        """Select optimal transport method based on availability and performance."""
        # Prefer local packages for better performance/security
        for package in self.packages:
            if package.registry_type in ["docker", "oci"]:  # OCI containers
                return "stdio"

        # Fallback to remote endpoints
        for remote in self.remotes:
            if remote.type == "sse":
                return "sse"
            if remote.type in ["streamable-http", "http"]:
                return "http"
            if remote.type == "websocket":
                return "websocket"

        # Provide helpful error message
        available_transports = []
        if self.packages:
            available_transports.extend([f"package:{pkg.registry_type}" for pkg in self.packages])
        if self.remotes:
            available_transports.extend([f"remote:{remote.type}" for remote in self.remotes])

        if available_transports:
            error_msg = (
                f"No supported transport for {self.name}. "
                f"Available: {available_transports}. "
                f"Supported: docker packages, sse/streamable-http/websocket remotes"
            )
        else:
            error_msg = (
                f"No transports available for {self.name}. Server metadata may be incomplete"
            )

        raise UnsupportedTransportError(error_msg)


class RegistryServerWrapper(Schema):
    """Wrapper for server data from the official registry API."""

    server: RegistryServer
    """The actual server data."""

    meta: dict[str, Any] = Field(default_factory=dict, alias="_meta")
    """Registry metadata."""


class RegistryListResponse(Schema):
    """Response from the registry list servers endpoint."""

    servers: list[RegistryServerWrapper]
    """List of wrapped server entries."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Response metadata."""


@dataclass(slots=True)
class ListServersCacheEntry:
    """Cache entry for list_servers results."""

    servers: list[RegistryServer]
    timestamp: float


@dataclass(slots=True)
class GetServerCacheEntry:
    """Cache entry for get_server results."""

    server: RegistryServer
    timestamp: float


class MCPRegistryClient:
    """Client for interacting with the MCP registry API."""

    def __init__(self, base_url: str = "https://registry.modelcontextprotocol.io") -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx2.AsyncClient(timeout=30.0)
        self._cache_lists: dict[str, ListServersCacheEntry] = {}
        self._cache_servers: dict[str, GetServerCacheEntry] = {}

    async def list_servers(
        self, search: str | None = None, status: str = "active"
    ) -> list[RegistryServer]:
        """List servers from registry with optional filtering."""
        cache_key = f"list_servers:{search}:{status}"

        # Check cache first
        if cache_key in self._cache_lists:
            cached_entry = self._cache_lists[cache_key]
            if time.time() - cached_entry.timestamp < CACHE_TTL:
                log.debug("Using cached server list")
                return cached_entry.servers

        try:
            log.info("Fetching server list from registry")
            response = await self.client.get(f"{self.base_url}/v0/servers")
            response.raise_for_status()
            data = response.json()
        except httpx2.HTTPError as e:
            raise MCPRegistryError(f"Failed to list servers: {e}") from e
        else:
            data = RegistryListResponse(**data)
            if status:  # Filter by status from metadata
                wrappers = [w for w in data.servers if w.meta.get(NAME, {}).get("status") == status]
            else:
                wrappers = data.servers
            servers = [wrapper.server for wrapper in wrappers]
            if search:  # Filter by search term
                lower = search.lower()
                servers = [s for s in servers if lower in s.name.lower() + s.description.lower()]
            # Cache the result
            ts = time.time()
            self._cache_lists[cache_key] = ListServersCacheEntry(servers=servers, timestamp=ts)
            log.info("Successfully fetched %d servers", len(servers))
            return servers

    async def get_server(self, server_id: str) -> RegistryServer:
        """Get full server details including packages."""
        cache_key = f"server:{server_id}"
        # Check cache first
        if cache_key in self._cache_servers:
            cached_entry = self._cache_servers[cache_key]
            if time.time() - cached_entry.timestamp < CACHE_TTL:
                log.debug("Using cached server details for %s", server_id)
                return cached_entry.server

        log.info("Fetching server details for %s", server_id)
        try:
            # Get all wrappers to access metadata
            response = await self.client.get(f"{self.base_url}/v0/servers")
            response.raise_for_status()
            data = response.json()
            response_data = RegistryListResponse.model_validate(data)
            # Find server by name
            target_wrapper = None
            for wrapper in response_data.servers:
                if wrapper.server.name == server_id:
                    target_wrapper = wrapper
                    break

            if not target_wrapper:
                raise MCPRegistryError(f"Server {server_id!r} not found in registry")
            # Get the UUID from metadata
            server_uuid = target_wrapper.meta.get(NAME, {}).get("id")
            if not server_uuid:
                raise MCPRegistryError(f"No UUID found for server {server_id!r}")
            # Now fetch the full server details using UUID
            response = await self.client.get(f"{self.base_url}/v0/servers/{server_uuid}")
            response.raise_for_status()
            server_data = response.json()
        except httpx2.HTTPStatusError as e:
            if e.response.status_code == HTTP_NOT_FOUND:
                raise MCPRegistryError(f"Server {server_id!r} not found in registry") from e

            raise MCPRegistryError(f"Failed to get server details: {e}") from e
        except (httpx2.HTTPError, ValueError, KeyError) as e:
            raise MCPRegistryError(f"Failed to get server details: {e}") from e
        else:
            server = RegistryServer.model_validate(server_data)
            ts = time.time()
            self._cache_servers[cache_key] = GetServerCacheEntry(server=server, timestamp=ts)
            log.info("Successfully fetched server details for %s", server_id)
            return server

    def clear_cache(self) -> None:
        """Clear the metadata cache."""
        self._cache_lists.clear()
        self._cache_servers.clear()
        log.debug("Cleared metadata cache")

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
        log.debug("Closed HTTP client")

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


class MCPRegistryError(Exception):
    """Exception raised for MCP registry operations."""


if __name__ == "__main__":

    async def main() -> None:
        """Test the MCP registry client with caching and transport resolution."""
        async with MCPRegistryClient() as client:
            print("Listing servers from official registry...")
            servers = await client.list_servers()
            print(f"Found {len(servers)} servers")

            # Test transport resolution for first few servers
            for server in servers[:3]:
                print(f"\n=== {server.name} ===")
                try:
                    transport = server.get_preferred_transport()
                    print(f"Preferred transport: {transport}")

                    # Show available transports
                    if server.packages:
                        print(f"Packages: {[p.registry_type for p in server.packages]}")
                    if server.remotes:
                        print(f"Remotes: {[r.type for r in server.remotes]}")

                except UnsupportedTransportError as e:
                    print(f"Transport error: {e}")

                print(server)

    anyio.run(main)
