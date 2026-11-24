"""The T3 storage provider reads an environment's threads as sessions (replayed ``/mcp``)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx2
from pyt3client import T3Client

from agentpool.models.manifest import AgentsManifest
from agentpool_config.storage import T3StorageConfig
from agentpool_storage.models import QueryFilters
from agentpool_storage.t3_provider import T3StorageProvider

from .test_t3_agent import BASE_URL, ReplayServer, _script


THREAD_ID = "mcp:f96a3a28-6468-4846-abad-9cfe3751148b"
PROJECT_ID = "mcp:c5281313-11d0-4b09-becb-25a7ac423ad8"


def _recorded(tool: str) -> dict[str, Any]:
    """The last result of ``tool`` in the recorded session."""
    return next(c for c in reversed(_script()) if c["tool"] == tool)


def _thread_list() -> dict[str, Any]:
    thread = _recorded("t3_thread_read")["result"]["thread"]
    result = {"projectId": None, "currentThreadId": None, "threads": [thread]}
    return {"tool": "t3_thread_list", "result": result | {"nextCursor": None, "total": 1}}


def _provider(
    script: list[dict[str, Any]], **settings: Any
) -> tuple[T3StorageProvider, ReplayServer]:
    server = ReplayServer(script)
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(server.handle))
    client = T3Client(BASE_URL, "token", http_client=http)
    return T3StorageProvider(T3StorageConfig(**settings), client=client), server


async def test_threads_are_sessions_with_their_messages() -> None:
    script = [_recorded("t3_project_list"), _thread_list(), _recorded("t3_thread_read")]
    provider, server = _provider(script)
    async with provider:
        [conversation] = await provider.get_sessions(QueryFilters(limit=5))
    assert conversation["id"] == THREAD_ID
    assert conversation["agent"] == "t3"
    assert conversation["title"] is not None
    assert conversation["title"].startswith("Run the shell command")
    messages = conversation["messages"]
    assert [(m.role, m.content) for m in messages] == [
        ("user", "What did the command print? One word."),
        ("assistant", "hello-from-t3"),
    ]
    assert messages[0].timestamp == datetime(2026, 11, 22, 5, 45, 54, 609000, tzinfo=UTC)
    assert server.script == []
    assert [name for name, _ in server.calls] == [
        "t3_project_list",
        "t3_thread_list",
        "t3_thread_read",
    ]
    assert server.calls[1][1]["allProjects"] is True  # No project: every project's threads


async def test_filters_apply_without_reading_threads_they_exclude() -> None:
    script = [_recorded("t3_project_list"), _thread_list()]
    provider, server = _provider(script)
    async with provider:
        assert await provider.get_sessions(QueryFilters(cwd="/elsewhere")) == []
        assert await provider.get_sessions(QueryFilters(agent_name="other")) == []
    assert [name for name, _ in server.calls] == ["t3_project_list", "t3_thread_list"]


async def test_a_thread_loads_as_session_data_in_its_projects_folder() -> None:
    script = [_recorded("t3_project_list"), _recorded("t3_thread_read")]
    provider, _ = _provider(script)
    async with provider:
        data = await provider.load_session(THREAD_ID)
    assert data is not None
    assert (data.session_id, data.sdk_session_id, data.agent_name) == (THREAD_ID, THREAD_ID, "t3")
    assert data.cwd == "/work/proj"


async def test_a_project_folder_limits_the_threads_to_that_project() -> None:
    projects = _recorded("t3_project_list")
    provider, server = _provider([projects, projects, _thread_list()], project="/work/proj")
    async with provider:
        assert await provider.list_session_ids() == [THREAD_ID]
        assert await provider.list_session_ids(agent_name="other") == []
    assert server.calls[-1] == (
        "t3_thread_list",
        {"projectId": PROJECT_ID, "limit": 100},
    )


def test_the_manifest_takes_a_t3_storage_provider() -> None:
    manifest = AgentsManifest.model_validate({
        "storage": {"providers": [{"type": "t3", "login": "homelab", "project": "/srv/app"}]}
    })
    [config] = manifest.storage.providers
    assert isinstance(config, T3StorageConfig)
    assert isinstance(config.get_provider(), T3StorageProvider)
