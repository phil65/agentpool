"""Model, effort and fast mode options of the Claude Code agent, from the CLI's model list."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clawd_code_sdk.models.server_info import ClaudeCodeServerInfo
import pytest

from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.claude_code_agent.config_options import closest_effort
from agentpool.agents.modes import ConfigOptionChanged


if TYPE_CHECKING:
    from clawd_code_sdk.models.settings import ClaudeCodeSettings


ALL_LEVELS = ["low", "medium", "high", "xhigh", "max"]

# As the CLI reports it (2026-11), except the trimmed effort levels of sonnet and
# haiku, which make the switches below move the effort.
SERVER_INFO = ClaudeCodeServerInfo.model_validate({
    "models": [
        {
            "value": "default",
            "display_name": "Default (recommended)",
            "description": "Opus",
            "resolved_model": "claude-opus-5-5",
            "supports_effort": True,
            "supported_effort_levels": ALL_LEVELS,
            "supports_fast_mode": True,
        },
        {
            "value": "opus",
            "display_name": "Opus",
            "description": "Opus",
            "resolved_model": "claude-opus-5-5",
            "supports_effort": True,
            "supported_effort_levels": ALL_LEVELS,
            "supports_fast_mode": True,
        },
        {
            "value": "claude-fable-5-2[1m]",
            "display_name": "Fable",
            "description": "Fable",
            "resolved_model": "claude-fable-5-2",
            "supports_effort": True,
            "supported_effort_levels": ALL_LEVELS,
            "supports_fast_mode": True,
        },
        {
            "value": "sonnet",
            "display_name": "Sonnet",
            "description": "Sonnet",
            "resolved_model": "claude-sonnet-5-5",
            "supports_effort": True,
            "supported_effort_levels": ["low", "medium", "high"],
        },
        {
            "value": "haiku",
            "display_name": "Haiku",
            "description": "Haiku",
            "resolved_model": "claude-haiku-5-5",
        },
    ],
    "fast_mode_state": "off",
    "fast_mode_disabled_reason": "sdk_opt_in_required",
})


class FakeClient:
    """The control requests of ClaudeSDKClient that the options send."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, Any]] = []

    async def apply_flag_settings(self, settings: ClaudeCodeSettings) -> None:
        flags = settings.model_dump(by_alias=True, exclude_unset=True, exclude_none=True)
        self.requests.append(("flags", flags))

    async def set_model(self, model: str) -> None:
        self.requests.append(("model", model))

    async def disconnect(self) -> None:
        raise AssertionError("options must not reconnect the CLI")


def _agent(**kwargs: Any) -> tuple[ClaudeCodeAgent[Any, Any], FakeClient]:
    agent = ClaudeCodeAgent(name="cc", **kwargs)
    agent._server_info = SERVER_INFO
    agent._fast_mode = False
    client = FakeClient()
    agent._client = client  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
    return agent, client


async def _option(agent: ClaudeCodeAgent[Any, Any], option_id: str) -> tuple[list[str], str]:
    """The values of an option and its current value (no values: not offered)."""
    categories = {category.id: category for category in await agent.get_modes()}
    if (category := categories.get(option_id)) is None:
        return [], ""
    return [str(m.value) for m in category.available_modes], category.current_mode_id


async def test_models_come_from_the_cli() -> None:
    agent, _ = _agent()
    models = await agent.get_available_models()
    assert [(m.id, m.name) for m in models] == [
        ("default", "Default (recommended)"),
        ("opus", "Opus"),
        ("claude-fable-5-2[1m]", "Fable"),
        ("sonnet", "Sonnet"),
        ("haiku", "Haiku"),
    ]
    assert [m.context_window for m in models] == [200_000, 200_000, 1_000_000, 200_000, 200_000]


async def test_effort_follows_the_model_and_applies_live() -> None:
    agent, client = _agent(reasoning_effort="max")
    assert await _option(agent, "thought_level") == (
        ["low", "medium", "high", "xhigh", "max"],
        "max",
    )

    await agent.set_effort("xhigh")
    assert client.requests == [("flags", {"effortLevel": "xhigh"})]

    # Sonnet has no xhigh: the effort moves to the closest level it has
    await agent.set_model("sonnet")
    assert client.requests[1:] == [("model", "sonnet"), ("flags", {"effortLevel": "high"})]
    assert await _option(agent, "thought_level") == (["low", "medium", "high"], "high")

    # Haiku has no effort setting: the option is not offered
    await agent.set_model("haiku")
    assert await _option(agent, "thought_level") == ([], "")


async def test_fast_mode_option() -> None:
    agent, client = _agent()
    changes: list[Any] = []

    async def on_change(change: Any) -> None:
        changes.append(change)

    agent.state_updated.connect(on_change)
    assert await _option(agent, "fast") == (["off", "on"], "off")

    await agent.set_mode("on", category_id="fast")
    # Reset first: the CLI ignores a repeated true
    assert client.requests == [("flags", {"fastMode": False}), ("flags", {"fastMode": True})]
    assert await _option(agent, "fast") == (["off", "on"], "on")

    # The CLI reports fast mode off (e.g. after a model switch): the option follows
    await agent._follow_fast_mode(enabled=False)
    assert await _option(agent, "fast") == (["off", "on"], "off")
    assert changes[-1] == ConfigOptionChanged(config_id="fast", value_id="off")

    await agent.set_model("sonnet")
    assert await _option(agent, "fast") == ([], "")


async def test_static_lists_before_connecting() -> None:
    agent = ClaudeCodeAgent(name="cc")
    assert await _option(agent, "thought_level") == (
        ["low", "medium", "high", "xhigh", "max"],
        "high",
    )
    assert await _option(agent, "fast") == ([], "")


@pytest.mark.parametrize(
    ("effort", "levels", "expected"),
    [
        ("max", ["low", "medium", "high"], "high"),
        ("xhigh", ["low", "high", "max"], "high"),
        ("low", ["medium", "high"], "medium"),
    ],
)
def test_closest_effort(effort: Any, levels: Any, expected: str) -> None:
    assert closest_effort(effort, levels) == expected
