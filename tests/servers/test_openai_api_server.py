"""Tests for the OpenAI-compatible API server (issue #46 and the dead-endpoint bug N1)."""

from __future__ import annotations

from typing import Any

import anyenv
from httpx2 import ASGITransport, AsyncClient
import pytest

from agentpool import AgentPool
from agentpool.models.manifest import AgentsManifest
from agentpool_server.openai_api_server.server import OpenAIAPIServer


MANIFEST: dict[str, Any] = {
    "agents": {"helper": {"type": "native", "model": "test"}},
    "storage": {"providers": [{"type": "memory"}]},
}
KEY = "s3cret"
AUTH = {"Authorization": f"Bearer {KEY}"}
CHAT = {"model": "helper", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
async def pool():
    async with AgentPool(AgentsManifest.model_validate(MANIFEST)) as p:
        yield p


def client_for(server: OpenAIAPIServer) -> AsyncClient:
    transport = ASGITransport(app=server.app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://localhost:8000")


def sse_data(text: str) -> list[str]:
    return [line[6:] for line in text.splitlines() if line.startswith("data: ")]


async def test_chat_completion_with_key(pool):
    server = OpenAIAPIServer(pool, api_key=KEY)
    async with client_for(server) as c:
        r = await c.post("/v1/chat/completions", headers=AUTH, json=CHAT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"]


async def test_chat_completion_streaming(pool):
    server = OpenAIAPIServer(pool, api_key=KEY)
    async with client_for(server) as c:
        r = await c.post("/v1/chat/completions", headers=AUTH, json={**CHAT, "stream": True})
    assert r.status_code == 200, r.text
    data = sse_data(r.text)
    assert data[-1] == "[DONE]"
    chunks = [anyenv.load_json(d, return_type=dict) for d in data[:-1]]
    text = "".join(ch["choices"][0]["delta"].get("content") or "" for ch in chunks)
    assert text
    assert not text.startswith("Error")
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": KEY}, {"Authorization": "Basic x"}],
)
async def test_auth_failures(pool, headers: dict[str, str]):
    server = OpenAIAPIServer(pool, api_key=KEY)
    async with client_for(server) as c:
        r = await c.post("/v1/chat/completions", headers=headers, json=CHAT)
        models = await c.get("/v1/models", headers=headers)
        # The key must not be accepted from the query string either
        query = await c.post(f"/v1/chat/completions?authorization=Bearer%20{KEY}", json=CHAT)
    assert r.status_code == 401
    assert models.status_code == 401
    assert query.status_code == 401


async def test_models_listing_requires_key(pool):
    server = OpenAIAPIServer(pool, api_key=KEY)
    async with client_for(server) as c:
        r = await c.get("/v1/models", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["object"] == "list"
    assert r.json()["data"]


async def test_no_key_configured_needs_no_auth(pool):
    server = OpenAIAPIServer(pool)
    async with client_for(server) as c:
        r = await c.post("/v1/chat/completions", json=CHAT)
    assert r.status_code == 200, r.text


def test_non_loopback_host_requires_key(pool):
    with pytest.raises(ValueError, match="api_key"):
        OpenAIAPIServer(pool, host="0.0.0.0")
    with pytest.raises(ValueError, match="api_key"):
        OpenAIAPIServer(pool, cors_origins=["*"])
    OpenAIAPIServer(pool, host="0.0.0.0", api_key=KEY)
    OpenAIAPIServer(pool, host="127.0.0.1")


async def test_cors_off_by_default(pool):
    server = OpenAIAPIServer(pool)
    async with client_for(server) as c:
        r = await c.post(
            "/v1/chat/completions", headers={"Origin": "https://evil.example"}, json=CHAT
        )
    assert "access-control-allow-origin" not in r.headers


async def test_cors_allows_only_configured_origins_without_credentials(pool):
    server = OpenAIAPIServer(pool, cors_origins=["https://app.example"], api_key=KEY)
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    async with client_for(server) as c:
        evil = await c.options(
            "/v1/chat/completions", headers={"Origin": "https://evil.example", **preflight}
        )
        good = await c.options(
            "/v1/chat/completions", headers={"Origin": "https://app.example", **preflight}
        )
    assert evil.headers.get("access-control-allow-origin") != "https://evil.example"
    assert good.status_code == 200
    assert good.headers["access-control-allow-origin"] == "https://app.example"
    assert "access-control-allow-credentials" not in good.headers


async def test_empty_messages_is_a_validation_error(pool):
    server = OpenAIAPIServer(pool)
    async with client_for(server) as c:
        r = await c.post("/v1/chat/completions", json={"model": "helper", "messages": []})
    assert r.status_code == 422


@pytest.mark.parametrize("stream", [False, True])
async def test_responses_bad_input_is_400(pool, stream: bool):
    server = OpenAIAPIServer(pool)
    async with client_for(server) as c:
        r = await c.post("/v1/responses", json={"model": "helper", "input": [], "stream": stream})
    assert r.status_code == 400, r.text


async def test_responses_continuation_id_set_after_completion(pool):
    server = OpenAIAPIServer(pool)
    async with client_for(server) as c:
        r = await c.post("/v1/responses", json={"model": "helper", "input": "hi"})
        assert r.status_code == 200, r.text
        first_id = r.json()["id"]
        r = await c.post(
            "/v1/responses",
            json={"model": "helper", "input": "more", "previous_response_id": first_id},
        )
        assert r.status_code == 200, r.text
        streamed = await c.post(
            "/v1/responses", json={"model": "helper", "input": "hi", "stream": True}
        )
    assert streamed.status_code == 200
    assert "response.completed" in streamed.text
    # The streamed response (completed) is now the one to continue from
    assert server._last_response_id is not None
    assert server._last_response_id in streamed.text


def test_openapi_schema_reads_auth_from_header_and_body_from_json(pool):
    schema = OpenAIAPIServer(pool, api_key=KEY).app.openapi()
    op = schema["paths"]["/v1/chat/completions"]["post"]
    params = {p["name"]: p["in"] for p in op.get("parameters", [])}
    assert params == {"authorization": "header"}
    assert "application/json" in op["requestBody"]["content"]
