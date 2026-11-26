"""Regression tests for toolset, command and client bugs (#63)."""

from __future__ import annotations

import asyncio
from importlib.metadata import EntryPoint
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from exxec import LocalExecutionEnvironment
from exxec.events import ProcessStartedEvent
import httpx2
from slashed import CommandStore

from agentpool import Agent, AgentContext, AgentPool, AgentsManifest
from agentpool.messaging.context import NodeContext
from agentpool.tool_impls.execute_code import ExecuteCodeTool
from agentpool.tool_impls.read import create_read_tool
from agentpool_bot.cron.cron_types import CronSchedule
from agentpool_bot.cron.service import CronService
from agentpool_commands.agents import CreateAgentCommand
from agentpool_toolsets import entry_points as ep_module
from agentpool_toolsets.builtin.debug import DebugTools
from agentpool_toolsets.cron import CronTools
from agentpool_toolsets.entry_points import EntryPointTools
from agentpool_toolsets.openapi import OpenAPITools


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import pytest


def _ctx(agent: Agent[Any]) -> AgentContext:
    return AgentContext(node=agent, tool_call_id="call_1", tool_name="tool", tool_input={})


class _HangingEnv:
    """Execution environment whose code never finishes."""

    started = asyncio.Event()

    async def _stream(self) -> AsyncIterator[Any]:
        yield ProcessStartedEvent(process_id="p1", command="python")
        self.started.set()
        await asyncio.sleep(3600)

    def stream_code(self, code: str) -> AsyncIterator[Any]:
        return self._stream()


async def test_execute_code_cancellation_propagates():
    """Cancelling execute_code raises CancelledError, not UnboundLocalError (#63.2a)."""
    agent = Agent[None](name="a", model="test")
    ctx = _ctx(agent)
    env = _HangingEnv()
    env.started = asyncio.Event()
    tool = ExecuteCodeTool(name="execute_code", env=env)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    task = asyncio.create_task(
        tool.execute_and_unwrap(ctx=ctx, code="print(1)", title="../../escape me")
    )
    await asyncio.wait_for(env.started.wait(), 5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("expected CancelledError")
    files = ctx.internal_fs.find("execute_code/scripts")
    assert files, "the script is still saved"
    # The title is slugified: no separators or ".." in the file name (#63.2b)
    assert all(".." not in f.removeprefix("/") for f in files)
    [meta_path] = [f for f in files if f.endswith(".json")]
    assert meta_path.endswith("_escape_me.json")
    meta = json.loads(ctx.internal_fs.cat(meta_path))
    assert meta["error"] == "Cancelled"


async def test_debug_introspection_empty_result_and_get_logs():
    """An empty return value is returned, not an AssertionError; get_logs is a tool (#63.3)."""
    tools = DebugTools()
    names = {t.name for t in await tools.get_tools()}
    assert "get_logs" in names
    agent = Agent[None](name="a", model="test")
    ctx = _ctx(agent)
    result = await tools.execute_introspection(
        ctx, MagicMock(), code="async def main():\n    return ''", title="a/b"
    )
    assert result == "(empty result)"
    assert any(f.endswith("_a_b.py") for f in ctx.internal_fs.find("debug/scripts"))


async def test_entry_point_tools_selects_by_module(monkeypatch: pytest.MonkeyPatch):
    """Each package's own 'tools' entry point is used, selected by ``module`` (#63.5)."""

    def tools_a() -> list[Any]:
        def tool_a() -> str:
            """A."""
            return "a"

        return [tool_a]

    def tools_b() -> list[Any]:
        def tool_b() -> str:
            """B."""
            return "b"

        return [tool_b]

    eps = [
        EntryPoint(name="tools", value="pkg_a.tools:get_tools", group="agentpool"),
        EntryPoint(name="tools", value="pkg_b.tools:get_tools", group="agentpool"),
    ]
    loaders = {"pkg_a.tools:get_tools": tools_a, "pkg_b.tools:get_tools": tools_b}
    monkeypatch.setattr(EntryPoint, "load", lambda self: loaders[self.value])
    monkeypatch.setattr(ep_module, "entry_points", lambda group, name: eps)
    assert [t.name for t in await EntryPointTools("pkg_b").get_tools()] == ["tool_b"]
    assert [t.name for t in await EntryPointTools("pkg_a").get_tools()] == ["tool_a"]
    missing = EntryPointTools("pkg_c")
    for _ in range(2):  # a failed lookup is not cached as "no tools"
        try:
            await missing.get_tools()
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")


async def test_read_negative_line(tmp_path: Path):
    """``line=-3`` returns the last 3 lines (#63.6a)."""
    path = tmp_path / "f.txt"
    path.write_text("\n".join(str(i) for i in range(1, 11)))
    tool = create_read_tool(env=LocalExecutionEnvironment())
    agent = Agent[None](name="a", model="test")
    result = await tool.execute_and_unwrap(ctx=_ctx(agent), path=str(path), line=-3)
    assert result == "8\n9\n10"
    result = await tool.execute_and_unwrap(ctx=_ctx(agent), path=str(path), line=2, limit=2)
    assert result == "2\n3"


async def test_create_agent_on_team():
    """/create-agent works when the current node is a team (#63.6b).

    (An explicit model: the default one needs an OpenAI key.)
    """
    manifest = AgentsManifest.from_yaml(
        "agents:\n  a:\n    type: native\n    model: test\n"
        "teams:\n  t:\n    mode: parallel\n    members: [a]\n"
    )
    async with AgentPool(manifest=manifest) as pool:
        messages: list[str] = []
        context = CommandStore().create_context(
            NodeContext(node=pool.teams["t"], pool=pool), output_writer=messages.append
        )
        await CreateAgentCommand().execute(
            ctx=context, args=["helper"], kwargs={"system_prompt": "help", "model": "test"}
        )
        assert "helper" in pool.all_agents


async def test_openapi_empty_response_and_close():
    """A 204 response gives ``{}``; leaving the provider closes its client (#63.6d)."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(204)

    provider = OpenAPITools(spec="unused.yaml", base_url="https://api.example.com")
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), base_url="https://api.example.com"
    )
    provider._client = client
    async with provider:
        assert await provider._make_request("DELETE", "/pet/1", {}, None) == {}
    assert client.is_closed
    assert provider._client is None


async def test_cron_job_runs_prompt_on_agent(tmp_path: Path):
    """A fired cron job sends its message to the agent the toolset belongs to (#63.1b)."""
    prompts: list[str] = []
    done = asyncio.Event()
    agent = Agent[None](name="a", model="test")

    async def run(prompt: str, **kwargs: Any) -> Any:
        prompts.append(prompt)
        done.set()
        return MagicMock(content="ok")

    agent.run = run  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    service = CronService(tmp_path / "jobs.json")
    provider = CronTools(service=service)
    provider.attach_agent(agent)
    async with provider:
        job = service.add_job("j", CronSchedule(kind="every", every_ms=20), "ping")
        await asyncio.wait_for(done.wait(), 5)
    assert prompts[0] == "ping"
    [stored] = service.list_jobs()
    assert stored.id == job.id
    assert stored.state.last_status == "ok"


async def test_cron_job_without_agent_is_an_error(tmp_path: Path):
    """Without an agent the job fails instead of silently reporting "ok" (#63.1b)."""
    service = CronService(tmp_path / "jobs.json")
    CronTools(service=service)
    job = service.add_job("j", CronSchedule(kind="every", every_ms=3_600_000), "ping")
    await service.run_job(job.id, force=True)
    [stored] = service.list_jobs()
    assert stored.state.last_status == "error"


async def test_native_agent_attaches_toolsets(tmp_path: Path):
    """The cron toolset configured on an agent is attached to that agent."""
    manifest = AgentsManifest.from_yaml(
        "agents:\n  a:\n    type: native\n    model: test\n    tools:\n"
        f"      - type: cron\n        store_path: {(tmp_path / 'j.json').as_posix()}\n"
    )
    async with AgentPool(manifest=manifest) as pool:
        agent = pool.get_agent("a")
        [cron] = [p for p in agent.tools.external_providers if isinstance(p, CronTools)]
        assert cron._agent is agent


def test_toad_stdio_quotes_config_path(monkeypatch: pytest.MonkeyPatch):
    """A config path with spaces stays one argument of the spawned server (#63.6f)."""
    import shlex

    from agentpool_cli import ui

    calls: list[list[str]] = []
    monkeypatch.setattr(ui.os, "system", lambda cmd: 0)
    monkeypatch.setattr(
        ui.subprocess, "run", lambda args, **kw: calls.append(args) or MagicMock(returncode=0)
    )
    ui._run_toad_stdio("my dir/agents.yml")
    assert shlex.split(calls[0][-1]) == ["agentpool", "serve-acp", "my dir/agents.yml"]


async def test_pi_rpc_client_stop_awaits_stderr_task():
    """``stop()`` cancels and awaits the stderr reader too (#63.6g)."""
    import sys

    from pi_sdk import RpcClient

    client = RpcClient()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    client._process = process
    stderr_task = asyncio.create_task(client._read_stderr())
    client._stderr_task = stderr_task
    await client.stop()
    assert stderr_task.done()
    assert client._stderr_task is None
