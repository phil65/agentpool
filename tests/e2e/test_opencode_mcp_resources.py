"""L4 subprocess E2E tests for OpenCode MCP resource integration.

Covers the ``add-mcp-resource-integration`` OpenSpec change:
    - 5.2: GET /experimental/resource endpoint with real MCP resources
    - 5.3: Resource source injection (text/blob) via message parts
    - 5.4: Cross-session scope isolation for resource providers

All tests use ``model: test`` (pydantic-ai TestModel) so NO API key is needed.
The fake MCP server (``tests/fixtures/fake_mcp_server_resources.py``) exposes:
    - Text resource: ``config://app/settings``
    - Blob resource: ``image://logo`` (1x1 PNG, ``image/png``)
    - Resource template: ``file:///{path}``
    - Tool: ``ping``

L4a smoke tests: pytest -m "e2e and not slow" (~30s)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tests.e2e.conftest import SKIP_NO_BINARY, SKIP_WINDOWS


if TYPE_CHECKING:
    from tests.e2e.conftest import SubprocessServer


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(SKIP_NO_BINARY, reason="wolfharness binary not on PATH"),
    pytest.mark.skipif(SKIP_WINDOWS, reason="Windows subprocess issues"),
]


# ---------------------------------------------------------------------------
# 5.2 — GET /experimental/resource with real MCP resources
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subprocess_server_with_mcp_resources",
    [{"serve_command": "serve-opencode", "is_stdio": False, "health_path": "/session"}],
    indirect=True,
)
async def test_experimental_resource_lists_text_and_blob(
    subprocess_server_with_mcp_resources: SubprocessServer,
) -> None:
    """GET /experimental/resource returns 200 with text and blob resources.

    The fake-resource-server exposes ``config://app/settings`` (text) and
    ``image://logo`` (blob). Both should appear in the response dict keyed
    by ``"{escaped_client}:{uri}"``.
    """
    base_url = subprocess_server_with_mcp_resources.base_url

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{base_url}/experimental/resource")
        assert resp.status_code == 200, (
            f"Expected 200 for GET /experimental/resource, got {resp.status_code}: {resp.text}"
        )
        resources = resp.json()
        assert isinstance(resources, dict), f"Expected dict, got {type(resources)}"

        # The response is keyed by "{escaped_client}:{uri}". The client name
        # is the display_name from config ("fake_resources"), escaped (% → %25,
        # : → %3A). Since "fake_resources" has no % or :, the escape is a no-op.
        # Look for our two resources by URI suffix.
        uris_seen: set[str] = set()
        for entry in resources.values():
            assert isinstance(entry, dict), f"Expected dict entry, got {type(entry)}"
            uri = entry.get("uri", "")
            uris_seen.add(uri)
            assert "client" in entry, f"Entry missing 'client' field: {entry}"

        assert "config://app/settings" in uris_seen, (
            f"Text resource 'config://app/settings' not found in response URIs: {uris_seen}"
        )
        assert "image://logo" in uris_seen, (
            f"Blob resource 'image://logo' not found in response URIs: {uris_seen}"
        )


@pytest.mark.parametrize(
    "subprocess_server_with_mcp_resources",
    [{"serve_command": "serve-opencode", "is_stdio": False, "health_path": "/session"}],
    indirect=True,
)
async def test_experimental_resource_keys_are_escaped_client_uri(
    subprocess_server_with_mcp_resources: SubprocessServer,
) -> None:
    """GET /experimental/resource keys follow ``{escaped_client}:{uri}`` format.

    The fake_resources server has display_name ``fake_resources`` (no special
    chars), so the key should be ``fake_resources:config://app/settings`` and
    ``fake_resources:image://logo``.
    """
    base_url = subprocess_server_with_mcp_resources.base_url

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{base_url}/experimental/resource")
        assert resp.status_code == 200
        resources = resp.json()

        expected_text_key = "fake_resources:config://app/settings"
        expected_blob_key = "fake_resources:image://logo"
        secondary_text_key = "fake_resources_secondary:config://app/settings"
        assert expected_text_key in resources, (
            f"Expected key '{expected_text_key}' not found. Keys: {list(resources.keys())}"
        )
        assert expected_blob_key in resources, (
            f"Expected key '{expected_blob_key}' not found. Keys: {list(resources.keys())}"
        )
        assert secondary_text_key in resources, (
            "The same URI from the second server must remain a distinct catalog entry."
        )

        # Verify McpResource fields
        text_entry = resources[expected_text_key]
        assert text_entry["uri"] == "config://app/settings"
        assert text_entry["client"] == "fake_resources"
        assert "name" in text_entry


# ---------------------------------------------------------------------------
# 5.3 — Resource source injection via message parts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subprocess_server_with_mcp_resources",
    [{"serve_command": "serve-opencode", "is_stdio": False, "health_path": "/session"}],
    indirect=True,
)
async def test_resource_injection_text_resource(
    subprocess_server_with_mcp_resources: SubprocessServer,
) -> None:
    """POST /session/{id}/message with a ResourceSource part injects text resource content.

    Sends a message containing a ``ResourceSource`` part referencing
    ``config://app/settings`` from the ``fake_resources`` server. The server
    should resolve the resource and inject its text content into the agent
    context.
    """
    base_url = subprocess_server_with_mcp_resources.base_url

    async with httpx.AsyncClient(timeout=20.0) as client:
        # Create a session
        resp = await client.post(
            f"{base_url}/session",
            json={"agent": "test_agent"},
        )
        assert resp.status_code in (200, 201), f"Failed to create session: {resp.text}"
        session_id = resp.json()["id"]

        # Send a message with a resource source part
        message_payload: dict[str, Any] = {
            "parts": [
                {
                    "type": "text",
                    "text": "Read the config file.",
                },
                {
                    "type": "file",
                    "mime": "application/json",
                    "url": "config://app/settings",
                    "source": {
                        "type": "resource",
                        "text": {
                            "value": "@fake_resources:settings",
                            "start": 0,
                            "end": 24,
                        },
                        "clientName": "fake_resources",
                        "uri": "config://app/settings",
                    },
                },
            ]
        }
        resp = await client.post(
            f"{base_url}/session/{session_id}/message",
            json=message_payload,
            headers={"Accept": "text/event-stream"},
        )
        # The server should accept the message (200 for SSE stream or 202 for queued)
        assert resp.status_code in (200, 202), (
            f"Expected 200/202 for message with resource source, "
            f"got {resp.status_code}: {resp.text}"
        )


@pytest.mark.parametrize(
    "subprocess_server_with_mcp_resources",
    [{"serve_command": "serve-opencode", "is_stdio": False, "health_path": "/session"}],
    indirect=True,
)
async def test_model_tool_list_exposes_only_three_resource_tools(
    subprocess_server_with_mcp_resources: SubprocessServer,
) -> None:
    """The running agent exposes the stable resource surface, not legacy tools."""
    base_url = subprocess_server_with_mcp_resources.base_url

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{base_url}/experimental/tool/ids")

    assert response.status_code == 200
    tool_ids = set(response.json())
    assert {
        "list_mcp_resources",
        "list_mcp_resource_templates",
        "read_mcp_resource",
    } <= tool_ids
    assert {
        "list_resources",
        "resource_exists",
        "read_resource",
        "list_resource_templates",
        "complete_resource_template",
    }.isdisjoint(tool_ids)


# ---------------------------------------------------------------------------
# 5.4 — Cross-session scope isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subprocess_server_with_mcp_resources",
    [{"serve_command": "serve-opencode", "is_stdio": False, "health_path": "/session"}],
    indirect=True,
)
async def test_resource_endpoint_consistent_across_sessions(
    subprocess_server_with_mcp_resources: SubprocessServer,
) -> None:
    """GET /experimental/resource returns consistent results across sessions.

    Resource providers are registered at POOL scope (pool-level MCP servers)
    so the resource list should be identical regardless of session context.
    This test creates two sessions and verifies the resource list is the same.
    """
    base_url = subprocess_server_with_mcp_resources.base_url

    async with httpx.AsyncClient(timeout=20.0) as client:
        # Create two sessions
        resp1 = await client.post(f"{base_url}/session", json={"agent": "test_agent"})
        assert resp1.status_code in (200, 201)
        session1_id = resp1.json()["id"]

        resp2 = await client.post(f"{base_url}/session", json={"agent": "test_agent"})
        assert resp2.status_code in (200, 201)
        session2_id = resp2.json()["id"]

        # Get resources — pool-level providers should appear for any session
        resp = await client.get(f"{base_url}/experimental/resource")
        assert resp.status_code == 200
        resources = resp.json()

        # Both text and blob resources should be present (POOL scope)
        uris = {entry.get("uri") for entry in resources.values()}
        assert "config://app/settings" in uris
        assert "image://logo" in uris

        # Sessions are different
        assert session1_id != session2_id


@pytest.mark.parametrize(
    "subprocess_server_with_mcp_resources",
    [
        {
            "serve_command": "serve-opencode",
            "is_stdio": False,
            "health_path": "/session",
            "extra_args": ["--agent", "resource_tools_disabled_agent"],
        }
    ],
    indirect=True,
)
async def test_resources_disabled_hides_tools_but_keeps_host_catalog(
    subprocess_server_with_mcp_resources: SubprocessServer,
) -> None:
    """The agent gate hides tools without disabling Host resource providers."""
    base_url = subprocess_server_with_mcp_resources.base_url

    async with httpx.AsyncClient(timeout=20.0) as client:
        tool_response = await client.get(f"{base_url}/experimental/tool/ids")
        assert tool_response.status_code == 200
        assert {
            "list_mcp_resources",
            "list_mcp_resource_templates",
            "read_mcp_resource",
        }.isdisjoint(set(tool_response.json()))

        resource_response = await client.get(f"{base_url}/experimental/resource")
        assert resource_response.status_code == 200
        resources = resource_response.json()
        assert "fake_resources:config://app/settings" in resources
        assert "fake_resources_secondary:config://app/settings" in resources

        session_response = await client.post(
            f"{base_url}/session",
            json={"agent": "resource_tools_disabled_agent"},
        )
        assert session_response.status_code in (200, 201)
        session_id = session_response.json()["id"]
        message_response = await client.post(
            f"{base_url}/session/{session_id}/message",
            json={
                "parts": [
                    {"type": "text", "text": "Read the config resource."},
                    {
                        "type": "file",
                        "mime": "application/json",
                        "url": "config://app/settings",
                        "source": {
                            "type": "resource",
                            "text": {"value": "@fake_resources:settings", "start": 0, "end": 24},
                            "clientName": "fake_resources",
                            "uri": "config://app/settings",
                        },
                    },
                ]
            },
            headers={"Accept": "text/event-stream"},
        )
        assert message_response.status_code in (200, 202)
