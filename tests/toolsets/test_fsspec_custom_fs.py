"""FSSpecTools with a custom filesystem never touch the host filesystem (#52.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fsspec.implementations.memory import MemoryFileSystem
import pytest

from agentpool import Agent, AgentContext
from agentpool_toolsets.fsspec_toolset import FSSpecTools


if TYPE_CHECKING:
    from pathlib import Path

    from agentpool.tools.base import Tool


@pytest.fixture
def memfs() -> MemoryFileSystem:
    fs = MemoryFileSystem(skip_instance_cache=True)
    fs.store = {}
    fs.pseudo_dirs = [""]
    fs.mkdir("/mem")
    fs.pipe("/mem/notes.txt", b"needle in memory\n")
    return fs


@pytest.fixture
def agent_ctx(tmp_path: Path) -> AgentContext:
    agent = Agent[None](name="fs_agent", model="test", env=str(tmp_path))
    return AgentContext(node=agent, tool_call_id="call_1", tool_name="t", tool_input={})


async def _tools(memfs: MemoryFileSystem) -> dict[str, Tool[Any]]:
    toolset = FSSpecTools(memfs, use_subprocess_grep=True)
    return {t.name: t for t in await toolset.get_tools()}


async def test_read_list_grep_use_custom_fs(memfs, agent_ctx):
    tools = await _tools(memfs)
    read = await tools["read"].execute_and_unwrap(ctx=agent_ctx, path="/mem/notes.txt")
    assert "needle in memory" in str(read)
    listing = await tools["list_directory"].execute_and_unwrap(ctx=agent_ctx, path="/mem")
    assert "notes.txt" in str(listing)
    grep = await tools["grep"].execute_and_unwrap(ctx=agent_ctx, pattern="needle", path="/mem")
    assert "notes.txt" in str(grep)


async def test_delete_does_not_touch_host_file(memfs, agent_ctx, tmp_path: Path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    tools = await _tools(memfs)
    await tools["delete_path"].execute_and_unwrap(ctx=agent_ctx, path=str(victim))
    assert victim.exists()
    await tools["delete_path"].execute_and_unwrap(ctx=agent_ctx, path="/mem/notes.txt")
    assert not memfs.exists("/mem/notes.txt")


async def test_relative_paths_ignore_agent_env_cwd(memfs, agent_ctx, tmp_path: Path):
    """Relative paths are not resolved against the agent env's (host) cwd."""
    (tmp_path / "mem").mkdir()
    (tmp_path / "mem" / "notes.txt").write_text("host content")
    tools = await _tools(memfs)
    read = await tools["read"].execute_and_unwrap(ctx=agent_ctx, path="mem/notes.txt")
    assert "needle in memory" in str(read)


@pytest.fixture
def fake_http(monkeypatch) -> list[dict[str, Any]]:
    """Serve every request from memory; record the client's constructor kwargs."""
    import httpx2

    created: list[dict[str, Any]] = []
    real_client = httpx2.AsyncClient

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"payload", headers={"Content-Length": "7"})

    class RecordingClient(real_client):  # type: ignore[misc,valid-type]
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)
            super().__init__(**kwargs, transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(httpx2, "AsyncClient", RecordingClient)
    return created


async def test_download_verifies_tls_and_writes_to_custom_fs(memfs, agent_ctx, fake_http):
    """Downloads verify TLS (#45.2) and land on the custom fs (#52.3)."""
    tools = await _tools(memfs)
    result = await tools["download_file"].execute_and_unwrap(
        ctx=agent_ctx, url="https://example.com/a/file.bin", target_dir="/mem/dl"
    )
    assert "error" not in result
    assert fake_http
    assert all(kwargs.get("verify", True) is not False for kwargs in fake_http)
    assert memfs.cat_file("/mem/dl/file.bin") == b"payload"


async def test_download_dotdot_name_and_instant_download(memfs, agent_ctx, fake_http, monkeypatch):
    """A URL ending in '..' gets a safe name; zero elapsed time doesn't divide by zero."""
    import time

    monkeypatch.setattr(time, "time", lambda: 1000.0)
    tools = await _tools(memfs)
    result = await tools["download_file"].execute_and_unwrap(
        ctx=agent_ctx, url="https://example.com/a/..", target_dir="/mem/dl"
    )
    assert "error" not in result, result
    assert result["filename"] == "downloaded_file"
