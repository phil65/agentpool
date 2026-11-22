"""Agents know their definition; sessions record its version and follow its changes (R2)."""

from __future__ import annotations

from typing import Any

from agentpool import AgentPool, AgentsManifest
from agentpool.agents.definition import AgentDefinition
from agentpool.agents.events import NoticeEvent
from agentpool.models.agents import NativeAgentConfig
from agentpool_config.storage import SQLStorageConfig, StorageConfig


def _manifest() -> AgentsManifest:
    storage = StorageConfig(providers=[SQLStorageConfig(url="sqlite+aiosqlite:///:memory:")])
    agents: dict[str, Any] = {"a": {"type": "native", "model": "test", "system_prompt": "Be kind."}}
    return AgentsManifest.model_validate({"agents": agents, "storage": storage.model_dump()})


def test_versions_follow_the_content_of_the_config() -> None:
    config = NativeAgentConfig(name="a", model="test", system_prompt="Be kind.")
    same = AgentDefinition.from_config(config.model_copy(update={"name": "b"}))
    changed = NativeAgentConfig(name="a", model="test", system_prompt="Be terse.")
    assert AgentDefinition.from_config(config).version == same.version  # Name aside
    assert AgentDefinition.from_config(changed).version != same.version


async def test_sessions_record_and_follow_the_definition_version() -> None:
    async with AgentPool(_manifest()) as pool:
        agent = pool.get_agent("a")
        assert agent.definition is not None
        first_version = agent.definition.version
        session = await pool.sessions.open("ses_r2", "a")
        events = [e async for e in agent.run_stream("hi", session=session)]
        assert not any(isinstance(e, NoticeEvent) for e in events)
        stored = await pool.storage.load_session("ses_r2")
        assert stored is not None
        assert stored.definition_version == first_version

        # Unchanged: the resumed session runs on without a notice
        pool.sessions.remove("ses_r2")
        session = await pool.sessions.open("ses_r2", "a")
        events = [e async for e in agent.run_stream("again", session=session)]
        assert not any(isinstance(e, NoticeEvent) for e in events)

        # Changed (e.g. a reloaded manifest): the session follows it, and says so
        changed = NativeAgentConfig(name="a", model="test", system_prompt="Be terse.")
        agent.definition = AgentDefinition.from_config(changed)
        pool.sessions.remove("ses_r2")
        session = await pool.sessions.open("ses_r2", "a")
        events = [e async for e in agent.run_stream("and now?", session=session)]
        notices = [e for e in events if isinstance(e, NoticeEvent)]
        assert len(notices) == 1
        assert "changed since this session last ran" in notices[0].title
        stored = await pool.storage.load_session("ses_r2")
        assert stored is not None
        assert stored.definition_version == agent.definition.version != first_version
        # Following on: once
        events = [e async for e in agent.run_stream("ok", session=session)]
        assert not any(isinstance(e, NoticeEvent) for e in events)
