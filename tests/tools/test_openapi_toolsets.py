from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import anyenv
from openapi_spec_validator import validate
import pytest

from agentpool_toolsets.openapi import OpenAPITools


if TYPE_CHECKING:
    from jsonschema_path.typing import Schema


BASE_URL = "https://api.example.com"
PETSTORE_SPEC: Schema = {
    "openapi": "3.0.0",
    "info": {"title": "Pet Store API", "version": "1.0.0"},
    "paths": {
        "/pet/{petId}": {
            "get": {
                "operationId": "get_pet",
                "summary": "Get pet by ID",
                "description": "Get pet by ID",
                "parameters": [
                    {
                        "name": "petId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                        "description": "ID of pet to find",
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Pet found",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id", "name"],
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "name": {"type": "string"},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        }
    },
    "servers": [{"url": BASE_URL, "description": "Pet store API server"}],
}


# Create mock httpx2 response
class MockResponse:
    """Mock httpx2 response."""

    def __init__(self):
        self.status_code = 200
        self._text = anyenv.dump_json(PETSTORE_SPEC)

    @property
    def text(self):
        return self._text

    def raise_for_status(self):
        pass


@pytest.fixture
def mock_openapi_spec(tmp_path):
    """Set up OpenAPI spec mocking and local file."""
    validate(PETSTORE_SPEC)
    local_spec = tmp_path / "openapi.json"  # Create local spec file
    local_spec.write_text(anyenv.dump_json(PETSTORE_SPEC))
    url = f"{BASE_URL}/openapi.json"
    return {"local_path": str(local_spec), "remote_url": url}


async def test_openapi_toolset_local(mock_openapi_spec):
    """Test OpenAPI toolset with local file."""
    local_path = mock_openapi_spec["local_path"]
    toolset = OpenAPITools(spec=local_path, base_url=BASE_URL)
    spec = await toolset._load_spec()  # Load and validate spec
    validate(spec)  # type: ignore[arg-type]
    tools = await toolset.get_tools()
    assert len(tools) == 1, f"Expected 1 tool, got {len(tools)}: {tools}"


async def test_openapi_toolset_remote(mock_openapi_spec, caplog, monkeypatch):
    """Test OpenAPI toolset with remote spec."""
    caplog.set_level("DEBUG")
    url = mock_openapi_spec["remote_url"]
    mock_response = MockResponse()

    # Mock sync httpx2.get (used by schemez load_openapi_spec)
    mock_sync_get = MagicMock(return_value=mock_response)
    monkeypatch.setattr("httpx2.get", mock_sync_get)

    # Mock async client for runtime requests
    mock_client = MagicMock()
    mock_async_get = AsyncMock(return_value=mock_response)
    mock_client.get = mock_async_get

    def mock_client_factory(*args, **kwargs):
        return mock_client

    monkeypatch.setattr("httpx2.AsyncClient", mock_client_factory)
    toolset = OpenAPITools(spec=url, base_url=BASE_URL)
    spec = await toolset._load_spec()
    validate(spec)  # type: ignore[arg-type]
    mock_sync_get.assert_called_once()  # Verify sync get was called for spec loading
    tools = await toolset.get_tools()
    assert len(tools) == 1, f"Expected 1 tool, got {len(tools)}: {tools}"


if __name__ == "__main__":
    pytest.main([__file__, "-vv"])
