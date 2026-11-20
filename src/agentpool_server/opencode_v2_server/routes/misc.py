"""Endpoints for OpenCode features AgentPool has no counterpart for.

Listing endpoints answer with empty collections so clients render normally; mutating
endpoints answer with a typed ``ServiceUnavailableError`` (503). Covered: plugins,
integrations, credentials, generation (text/image/video/decisions), voice, client MCP,
persistent PTYs, media/blobs, automations, artifacts, references, web search, usage
limits, plugin RPC and debugging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from agentpool_server.opencode_v2_server.deps import DirectoryDep, StateDep, V2Response, ok
from agentpool_server.opencode_v2_server.errors import ApiError, unavailable
from agentpool_server.opencode_v2_server.events import dump_model
from agentpool_server.opencode_v2_server.location import public_ref
from opencode_v2_sdk.models import (
    BlobNotFoundErrorEncoded,
    CredentialListResponse,
    ExperimentalArtifactsListResponse,
    ExperimentalArtifactsProvidersResponse,
    ExperimentalAutomationListResponse,
    ExperimentalPersistentPtyListResponse,
    ExperimentalUsageLimitsResponse,
    IntegrationListResponse,
    PluginListResponse,
    ReferenceListResponse,
    RpcErrorEncoded,
    WebsearchProvidersResponse,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


router = APIRouter(tags=["misc"], default_response_class=V2Response)


# --- listings (empty) ----------------------------------------------------------------------


@router.get("/api/plugin")
async def plugin_list(directory: DirectoryDep) -> Response:
    """Plugins (AgentPool extends agents via toolsets, not OpenCode plugins)."""
    return ok(PluginListResponse(location=public_ref(directory), data=[]))


@router.get("/api/integration")
async def integration_list(directory: DirectoryDep) -> Response:
    """Provider integrations (credentials come from the AgentPool configuration)."""
    return ok(IntegrationListResponse(location=public_ref(directory), data=[]))


@router.get("/api/credential")
async def credential_list() -> Response:
    """Stored credentials (none: credentials come from the AgentPool configuration)."""
    return ok(CredentialListResponse(data=[]))


@router.get("/api/reference")
async def reference_list(directory: DirectoryDep) -> Response:
    """Referenced external repositories."""
    return ok(ReferenceListResponse(location=public_ref(directory), data=[]))


@router.get("/api/websearch/provider")
async def websearch_providers(directory: DirectoryDep) -> Response:
    """Web search providers (web search is provided by agent toolsets)."""
    return ok(WebsearchProvidersResponse(location=public_ref(directory), data=[]))


@router.get("/api/experimental/usage/limits")
async def usage_limits(directory: DirectoryDep) -> Response:
    """Subscription usage limits (not tracked)."""
    return ok(ExperimentalUsageLimitsResponse(location=public_ref(directory), data=None))


@router.get("/api/experimental/automation")
async def automation_list() -> Response:
    """Scheduled automations (use AgentPool jobs/triggers instead)."""
    return ok(ExperimentalAutomationListResponse(data=[]))


@router.get("/api/experimental/artifacts")
async def artifacts_list(directory: DirectoryDep) -> Response:
    """Artifacts."""
    return ok(ExperimentalArtifactsListResponse(location=public_ref(directory), data=[]))


@router.get("/api/experimental/artifacts/providers")
async def artifacts_providers(directory: DirectoryDep) -> Response:
    """Artifact providers."""
    return ok(ExperimentalArtifactsProvidersResponse(location=public_ref(directory), data=[]))


@router.get("/api/experimental/session/{session_id}/terminal")
async def persistent_pty_list(session_id: str) -> Response:
    """Persistent session terminals (not supported; see ``serverInfo.capabilities``)."""
    return ok(ExperimentalPersistentPtyListResponse(data=[]))


@router.get("/api/experimental/migration/v1")
async def migration_status() -> Response:
    """V1 data migration status (nothing to migrate)."""
    return JSONResponse({"status": "completed"})


@router.get("/api/debug/location")
async def debug_locations(state: StateDep) -> Response:
    """Locations with loaded sessions."""
    directories = sorted({s.directory for s in state.sessions.values()} | {state.working_dir})
    return JSONResponse([dump_model(public_ref(d)) for d in directories])


@router.delete("/api/debug/location", status_code=204)
async def debug_evict(directory: DirectoryDep) -> Response:
    """Evict a location (nothing is cached per location)."""
    return Response(status_code=204)


@router.get("/api/experimental/media/{media_hash}")
async def media_get(media_hash: str) -> Response:
    """Media blobs are not stored separately (attachments are inline)."""
    message = f"Media not found: {media_hash}"
    raise ApiError(404, BlobNotFoundErrorEncoded(blob_id=media_hash, message=message))


@router.get("/api/experimental/blob/{blob_id}")
async def blob_get(blob_id: str) -> Response:
    """Blobs are not stored separately (attachments are inline)."""
    raise ApiError(
        404, BlobNotFoundErrorEncoded(blob_id=blob_id, message=f"Blob not found: {blob_id}")
    )


@router.post("/api/rpc/{rpc_id}/{method}")
async def rpc_call(rpc_id: str, method: str) -> Response:
    """Plugin RPC: no OpenCode plugins run here, so every call fails with ``RpcError``."""
    message = f"No plugin {rpc_id!r} (method {method!r}) on this server"
    raise ApiError(400, RpcErrorEncoded(type="NotFound", message=message))


# --- unsupported mutations -----------------------------------------------------------------

UNSUPPORTED: list[tuple[str, str, str]] = [
    ("GET", "/api/plugin/gui", "plugins"),
    ("POST", "/api/plugin/check", "plugins"),
    ("POST", "/api/plugin/update", "plugins"),
    ("POST", "/api/experimental/generate/text", "generation"),
    ("POST", "/api/experimental/generate", "generation"),
    ("POST", "/api/experimental/generate/image", "image generation"),
    ("POST", "/api/experimental/generate/video", "video generation"),
    ("GET", "/api/experimental/generate/video/{video_id}", "video generation"),
    ("POST", "/api/experimental/generate/decisions", "decision generation"),
    ("GET", "/api/experimental/voice/models", "voice"),
    ("POST", "/api/experimental/voice/session", "voice"),
    ("GET", "/api/integration/{integration_id}", "integrations"),
    ("POST", "/api/experimental/integration/wellknown", "integrations"),
    ("POST", "/api/integration/{integration_id}/connect/key", "integrations"),
    ("POST", "/api/integration/{integration_id}/connect/external", "integrations"),
    ("POST", "/api/integration/{integration_id}/connect/oauth", "integrations"),
    ("GET", "/api/integration/{integration_id}/connect/oauth/{attempt_id}", "integrations"),
    ("DELETE", "/api/integration/{integration_id}/connect/oauth/{attempt_id}", "integrations"),
    (
        "POST",
        "/api/integration/{integration_id}/connect/oauth/{attempt_id}/complete",
        "integrations",
    ),
    ("POST", "/api/integration/{integration_id}/connect/command", "integrations"),
    ("GET", "/api/integration/{integration_id}/connect/command/{attempt_id}", "integrations"),
    ("DELETE", "/api/integration/{integration_id}/connect/command/{attempt_id}", "integrations"),
    ("POST", "/api/credential", "credentials"),
    ("PATCH", "/api/credential/{credential_id}", "credentials"),
    ("DELETE", "/api/credential/{credential_id}", "credentials"),
    ("POST", "/api/credential/{credential_id}/activate", "credentials"),
    ("PUT", "/api/experimental/mcp/{server}", "MCP configuration"),
    ("DELETE", "/api/experimental/mcp/{server}", "MCP configuration"),
    ("POST", "/api/experimental/mcp/{server}/connect", "MCP configuration"),
    ("POST", "/api/experimental/mcp/{server}/disconnect", "MCP configuration"),
    ("POST", "/api/experimental/client-mcp/token", "client MCP"),
    ("GET", "/api/experimental/client-mcp", "client MCP"),
    ("GET", "/api/experimental/session/{session_id}/terminal/read", "persistent PTY"),
    ("POST", "/api/experimental/session/{session_id}/terminal", "persistent PTY"),
    ("POST", "/api/experimental/persistent-pty/shutdown", "persistent PTY"),
    ("POST", "/api/experimental/persistent-pty/handoff", "persistent PTY"),
    ("GET", "/api/experimental/persistent-pty/{pty_id}", "persistent PTY"),
    ("PUT", "/api/experimental/persistent-pty/{pty_id}", "persistent PTY"),
    ("DELETE", "/api/experimental/persistent-pty/{pty_id}", "persistent PTY"),
    ("GET", "/api/experimental/persistent-pty/{pty_id}/snapshot", "persistent PTY"),
    ("POST", "/api/experimental/persistent-pty/{pty_id}/connect-token", "persistent PTY"),
    ("GET", "/api/experimental/persistent-pty/{pty_id}/connect", "persistent PTY"),
    ("POST", "/api/experimental/automation", "automations"),
    ("GET", "/api/experimental/automation/{automation_id}", "automations"),
    ("DELETE", "/api/experimental/automation/{automation_id}", "automations"),
    ("POST", "/api/experimental/automation/{automation_id}/pause", "automations"),
    ("POST", "/api/experimental/automation/{automation_id}/resume", "automations"),
    ("POST", "/api/experimental/artifacts", "artifacts"),
    ("GET", "/api/experimental/artifacts/{artifact_id}", "artifacts"),
    ("PATCH", "/api/experimental/artifacts/{artifact_id}", "artifacts"),
    ("DELETE", "/api/experimental/artifacts/{artifact_id}", "artifacts"),
    ("GET", "/api/experimental/artifacts/{artifact_id}/view/{path:path}", "artifacts"),
    ("POST", "/api/websearch", "web search"),
    ("POST", "/api/debug/heap-dump", "heap dumps"),
]


def _unsupported_handler(service: str) -> Callable[[], Coroutine[Any, Any, Response]]:
    async def handler() -> Response:
        raise unavailable(service)

    handler.__doc__ = f"{service.capitalize()} are not supported by AgentPool (503)."
    return handler


for _method, _path, _service in UNSUPPORTED:
    router.add_api_route(
        _path,
        _unsupported_handler(_service),
        methods=[_method],
        response_model=None,
        include_in_schema=True,
    )
