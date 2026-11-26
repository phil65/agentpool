"""Regression tests for AgentPool and team wiring (issue #58)."""

from typing import TYPE_CHECKING, Any, Self

from agentpool import Agent, AgentPool, AgentsManifest, Team, TeamRun
from agentpool.agents.events import StreamCompleteEvent, SubAgentEvent


if TYPE_CHECKING:
    from pathlib import Path


AGENT_FILE = """\
---
name: reviewer
description: Reviews code
model: test
---
You review code.
"""


async def test_file_agents_are_pool_agents(tmp_path: Path):
    path = tmp_path / "reviewer.md"
    path.write_text(AGENT_FILE)
    manifest = AgentsManifest.model_validate({
        "agents": {"a": {"type": "native", "model": "test"}},
        "file_agents": {"rev": str(path)},
    })
    async with AgentPool(manifest) as pool:
        assert set(pool.all_agents) == {"a", "rev"}


async def test_get_agent_keeps_deps_and_output_type():
    manifest = AgentsManifest.model_validate({
        "agents": {
            "a": {
                "type": "native",
                "model": "test",
                "output_type": {
                    "response_schema": {"type": "inline", "fields": {"x": {"type": "int"}}}
                },
            }
        }
    })
    async with AgentPool(manifest, shared_deps_type=int) as pool:
        agent = pool.all_agents["a"]
        output_type = agent._output_type  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
        assert output_type is not str
        pool.get_agent("a")
        assert agent.deps_type is int
        assert agent._output_type is output_type  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
        pool.get_agent("a", output_type=str)
        assert agent._output_type is str  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]


async def test_team_cancel_is_a_method():
    async with AgentPool() as pool:
        a, b = Agent("a", model="test"), Agent("b", model="test")
        await pool.add_agent(a)
        await pool.add_agent(b)
        team = Team([a, b])
        await team.cancel()


async def test_add_agent_registers_before_entering():
    seen: dict[str, Any] = {}

    class Probe(Agent[None, str]):
        async def __aenter__(self) -> Self:
            seen["pool"] = self.agent_pool
            return await super().__aenter__()

    async with AgentPool() as pool:
        await pool.add_agent(Probe(name="p", model="test"))
        assert seen["pool"] is pool
        assert any(talk.source.name == "p" for talk in pool.pool_talk.iter_talks())


async def test_team_members_get_team_mcp_provider():
    manifest = AgentsManifest.model_validate({
        "agents": {"a": {"type": "native", "model": "test"}},
        "teams": {"t": {"mode": "parallel", "members": ["a"]}},
    })
    pool = AgentPool(manifest)
    team = pool.teams["t"]
    provider_name = team.mcp.get_aggregating_provider().name
    assert provider_name in [p.name for p in pool.all_agents["a"].tools.providers]


async def test_team_connections_may_target_teams():
    manifest = AgentsManifest.model_validate({
        "agents": {
            "a": {
                "type": "native",
                "model": "test",
                "connections": [{"type": "node", "name": "t"}],
            },
            "b": {"type": "native", "model": "test"},
        },
        "teams": {"t": {"mode": "parallel", "members": ["b"]}},
    })
    pool = AgentPool(manifest)
    targets = [
        t for talk in pool.all_agents["a"].connections.get_connections() for t in talk.targets
    ]
    assert targets == [pool.teams["t"]]


def _echo(prefix: str):
    def echo(prompt: str) -> str:
        return f"{prefix}({prompt})"

    return echo


async def _final_contents(run: TeamRun[Any, Any], prompt: str) -> list[tuple[str, int, Any]]:
    return [
        (event.source_name, event.depth, event.event.message.content)
        async for event in run.run_stream(prompt)
        if isinstance(event, SubAgentEvent) and isinstance(event.event, StreamCompleteEvent)
    ]


async def test_team_run_stream_shared_prompt_and_validator():
    async with AgentPool() as pool:
        a = pool.get_agent(Agent.from_callback(_echo("a"), name="a"))
        v = pool.get_agent(Agent.from_callback(_echo("v"), name="v"))
        run = TeamRun([a], validator=v, shared_prompt="shared")
        results = await _final_contents(run, "task")
        assert [name for name, _, _ in results] == ["a", "v"]
        assert "shared" in results[0][2]
        assert "task" in results[0][2]
        assert results[1][2] == f"v({results[0][2]})"


async def test_team_run_stream_passes_nested_team_result_on():
    async with AgentPool() as pool:
        inner_a = pool.get_agent(Agent.from_callback(_echo("x"), name="x"))
        inner_b = pool.get_agent(Agent.from_callback(_echo("y"), name="y"))
        last = pool.get_agent(Agent.from_callback(_echo("z"), name="z"))
        run = TeamRun([TeamRun([inner_a, inner_b], name="inner"), last])
        results = await _final_contents(run, "go")
        assert results[-1] == ("z", 1, "z(y(x(go)))")


async def test_team_stream_shared_prompt():
    async with AgentPool() as pool:
        a = pool.get_agent(Agent.from_callback(_echo("a"), name="a"))
        team = Team([a], shared_prompt="shared")
        contents = [
            event.event.message.content
            async for event in team.run_stream("task")
            if isinstance(event, SubAgentEvent) and isinstance(event.event, StreamCompleteEvent)
        ]
        assert len(contents) == 1
        assert "shared" in contents[0]
        assert "task" in contents[0]


async def test_team_run_execute_forwards_kwargs(monkeypatch):
    seen: dict[str, Any] = {}
    run = TeamRun([Agent("a", model="test")])

    async def execute_iter(*prompts: Any, **kwargs: Any):
        seen.update(kwargs)
        return
        yield

    monkeypatch.setattr(run, "execute_iter", execute_iter)
    await run.execute("x", store_history=False)
    assert seen == {"store_history": False}
