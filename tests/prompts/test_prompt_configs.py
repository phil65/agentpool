"""Tests for the `system_prompt` config types (static, file, function, library)."""

from typing import TYPE_CHECKING

from pydantic import ValidationError
import pytest

from agentpool import Agent, AgentsManifest
from agentpool.models.agents import NativeAgentConfig
from agentpool_config.system_prompts import (
    FilePromptConfig,
    FunctionPromptConfig,
    StaticPromptConfig,
)


if TYPE_CHECKING:
    from pathlib import Path


FUNCTION = "tests.prompts.helpers_prompt_fn:make_prompt"


def test_file_prompt_needs_only_path():
    prompt = FilePromptConfig(path="prompts/system.j2")
    assert prompt.variables == {}
    assert not hasattr(prompt, "content")


def test_function_prompt_needs_only_function():
    prompt = FunctionPromptConfig.model_validate({"function": FUNCTION})
    assert prompt.arguments == {}
    assert prompt.function() == "You are a neutral assistant."


def test_static_prompt_requires_content():
    with pytest.raises(ValidationError, match="content"):
        StaticPromptConfig.model_validate({})


def test_file_prompt_requires_path():
    with pytest.raises(ValidationError, match="path"):
        FilePromptConfig.model_validate({"content": "text"})


def test_manifest_with_file_and_function_prompts():
    manifest = AgentsManifest.model_validate({
        "agents": {
            "a": {
                "type": "native",
                "model": "test",
                "system_prompt": [
                    "Plain text",
                    {"type": "file", "path": "prompts/system.j2", "variables": {"x": 1}},
                    {"type": "function", "function": FUNCTION, "arguments": {"tone": "calm"}},
                ],
            }
        }
    })
    prompts = manifest.agents["a"].system_prompt
    assert isinstance(prompts, list)
    assert isinstance(prompts[1], FilePromptConfig)
    assert isinstance(prompts[2], FunctionPromptConfig)


def _config(tmp_path: Path) -> NativeAgentConfig:
    (tmp_path / "system.j2").write_text("Expert in {{ domain }}.", encoding="utf-8")
    return NativeAgentConfig.model_validate({
        "name": "a",
        "model": "test",
        "config_file_path": str(tmp_path / "agents.yml"),
        "system_prompt": [
            {"type": "file", "path": "system.j2", "variables": {"domain": "Python"}},
            {"type": "function", "function": FUNCTION, "arguments": {"role": "reviewer"}},
        ],
    })


def test_render_system_prompts(tmp_path: Path):
    """File prompts load relative to the config file; function prompts are called."""
    rendered = _config(tmp_path).render_system_prompts()
    assert rendered == ["Expert in Python.", "You are a neutral reviewer."]


async def test_agent_from_config_uses_file_and_function_prompts(tmp_path: Path):
    agent = Agent.from_config(_config(tmp_path))
    result = await agent.sys_prompts.format_system_prompt(agent)
    assert "Expert in Python." in result
    assert "You are a neutral reviewer." in result
