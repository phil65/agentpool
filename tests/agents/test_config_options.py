"""Tests for the declarative config options behind get_modes()/set_mode()."""

import pytest

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.codex_agent import CodexAgent
from agentpool.agents.exceptions import UnknownCategoryError, UnknownModeError
from agentpool.agents.modes import ConfigOptionChanged
from agentpool.agents.native_agent import Agent


async def test_native_modes_come_from_config_options():
    agent = Agent(name="n", model="test")
    modes = await agent.get_modes()
    assert [m.id for m in modes] == [o.id for o in agent.config_options]
    assert all(info.category_id == m.id for m in modes for info in m.available_modes)


async def test_set_mode_applies_and_emits_change():
    agent = Agent(name="n", model="test")
    changes: list[ConfigOptionChanged] = []

    async def on_change(change: ConfigOptionChanged) -> None:
        changes.append(change)

    agent.state_updated.connect(on_change)
    await agent.set_mode("never", category_id="mode")
    assert agent.tool_confirmation_mode == "never"
    assert changes == [ConfigOptionChanged(config_id="mode", value_id="never")]


async def test_set_mode_rejects_unknown_value_and_category():
    agent = Agent(name="n", model="test")
    with pytest.raises(UnknownModeError):
        await agent.set_mode("sometimes", category_id="mode")
    with pytest.raises(UnknownModeError):
        await agent.set_mode(True, category_id="mode")
    with pytest.raises(UnknownCategoryError):
        await agent.set_mode("x", category_id="nope")
    assert agent.tool_confirmation_mode == "per_tool"


async def test_native_model_option_accepts_models_outside_the_list(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    agent = Agent(name="n", model="test")
    await agent.set_mode("openai:my-custom-model", category_id="model")
    assert agent.model_name == "openai:my-custom-model"


async def test_claude_code_options_without_client():
    agent = ClaudeCodeAgent(name="c")
    await agent.set_mode("plan", category_id="mode")
    await agent.set_mode("xhigh", category_id="thought_level")
    modes = {m.id: m.current_mode_id for m in await agent.get_modes()}
    assert modes["mode"] == "plan"
    assert modes["thought_level"] == "xhigh"
    with pytest.raises(UnknownModeError):
        await agent.set_mode("gpt-5", category_id="model")


async def test_codex_options_without_client():
    agent = CodexAgent(name="x")
    await agent.set_mode("read-only", category_id="sandbox")
    await agent.set_mode("high", category_id="thought_level")
    await agent.set_mode("friendly", category_id="personality")
    modes = {m.id: m.current_mode_id for m in await agent.get_modes()}
    assert modes["sandbox"] == "read-only"
    assert modes["thought_level"] == "high"
    assert modes["personality"] == "friendly"
    with pytest.raises(UnknownModeError):
        await agent.set_mode("external-sandbox", category_id="sandbox")
