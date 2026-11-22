"""Tests for AntigravityAgentConfig and manifest integration."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from agentpool.agents.antigravity_agent import AntigravityAgent
from agentpool.agents.antigravity_agent.static_info import DEFAULT_MODEL
from agentpool.models.antigravity_agents import AntigravityAgentConfig, AntigravityBudgetConfig
from agentpool.models.manifest import AgentsManifest


def test_manifest_loads_antigravity_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_GEMINI_KEY", "gk-secret")
    monkeypatch.setenv("TEST_HEADER_VAL", "bearer-xyz")
    monkeypatch.setenv("TEST_SUBPROC_VAR", "enabled")
    manifest = AgentsManifest.model_validate({
        "agents": {
            "agy": {
                "type": "antigravity",
                "model": "gemini-3.1-pro-preview",
                "thinking_level": "high",
                "service_tier": "priority",
                "api_key": "${TEST_GEMINI_KEY}",
                "http_headers": {"X-Auth": "${TEST_HEADER_VAL}"},
                "env_vars": {"MY_FLAG": "${TEST_SUBPROC_VAR}"},
                "policy_mode": "confirm_run_command",
                "behavior": "interactive",
                "enable_sandbox": True,
                "budget": {"max_model_calls": 10, "max_total_tokens": 50000},
            }
        }
    })
    config = manifest.agents["agy"]
    assert isinstance(config, AntigravityAgentConfig)
    assert config.get_api_key() == "gk-secret"
    assert config.get_http_headers() == {"X-Auth": "bearer-xyz"}
    assert config.get_env_vars() == {"MY_FLAG": "enabled"}
    assert config.budget == AntigravityBudgetConfig(max_model_calls=10, max_total_tokens=50000)

    agent = config.get_agent()
    assert isinstance(agent, AntigravityAgent)
    assert agent.AGENT_TYPE == "antigravity"
    assert agent.model_name == "gemini-3.1-pro-preview"


def test_default_model_when_omitted() -> None:
    agent = AntigravityAgentConfig().get_agent()
    assert agent.model_name == DEFAULT_MODEL


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        (
            {"enabled_tools": ["view_file"], "disabled_tools": ["run_command"]},
            "mutually exclusive",
        ),
        (
            {"openai_compatible": True},
            "requires `base_url`",
        ),
        (
            {"vertex": True, "api_key": "k", "project": "p", "location": "global"},
            "Express Mode",
        ),
        (
            {"enable_subagents": False, "max_subagent_depth": 2},
            "requires `enable_subagents: true`",
        ),
    ],
)
def test_invalid_configs(fields: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        AntigravityAgentConfig.model_validate(fields)


async def test_build_sdk_config_maps_fields(tmp_path) -> None:
    from google.antigravity import (
        AgentBehavior,
        BuiltinTools,
        CustomSystemInstructions,
        GeminiAPIEndpoint,
        LocalAgentConfig,
        ServiceTier,
        ThinkingLevel,
    )

    config = AntigravityAgentConfig(
        model="gemini-3.1-pro-preview",
        thinking_level="high",
        service_tier="priority",
        max_output_tokens=4096,
        api_key="test-key",
        system_prompt="Only answer in JSON.",
        include_builtin_system_prompt=False,
        behavior="autonomous",
        policy_mode="allow_all",
        workspace_only=True,
        disabled_tools=["generate_image"],
        enable_sandbox=True,
        enable_daemons=True,
        command_timeout=120.0,
        tool_output_max_tokens=8000,
        mcp_max_output_bytes=16384,
        workspaces=[str(tmp_path)],
        save_dir=str(tmp_path / "saves"),
        compaction_token_threshold=64000,
        budget=AntigravityBudgetConfig(max_model_calls=5, max_tool_calls=20),
    )
    agent = config.get_agent()
    sdk_cfg = await agent._build_sdk_config()
    assert isinstance(sdk_cfg, LocalAgentConfig)
    assert isinstance(sdk_cfg.system_instructions, CustomSystemInstructions)
    assert "Only answer in JSON." in sdk_cfg.system_instructions.text
    assert sdk_cfg.capabilities.agent_behavior == AgentBehavior.AUTONOMOUS
    assert sdk_cfg.capabilities.disabled_tools == [BuiltinTools.GENERATE_IMAGE]
    assert sdk_cfg.capabilities.run_command_config is not None
    assert sdk_cfg.capabilities.run_command_config.enable_sandbox is True
    assert sdk_cfg.capabilities.run_command_config.enable_daemons is True
    assert sdk_cfg.capabilities.run_command_config.timeout_seconds == 120.0
    assert sdk_cfg.capabilities.tool_output_truncation_config is not None
    assert sdk_cfg.capabilities.tool_output_truncation_config.max_tokens == 8000
    assert sdk_cfg.capabilities.mcp_max_output_bytes == 16384
    assert sdk_cfg.compaction_config is not None
    assert sdk_cfg.compaction_config.token_threshold == 64000
    assert sdk_cfg.budget_config is not None
    assert sdk_cfg.budget_config.max_model_calls == 5
    assert sdk_cfg.budget_config.max_tool_calls == 20
    assert sdk_cfg.models is not None
    text_model = sdk_cfg.models[0]
    assert text_model.name == "gemini-3.1-pro-preview"
    assert isinstance(text_model.endpoint, GeminiAPIEndpoint)
    assert text_model.endpoint.api_key == "test-key"
    assert text_model.endpoint.options is not None
    assert text_model.endpoint.options.thinking_level == ThinkingLevel.HIGH
    assert text_model.endpoint.options.service_tier == ServiceTier.PRIORITY
    assert text_model.endpoint.options.max_output_tokens == 4096


async def test_build_sdk_config_openai_compatible(tmp_path) -> None:
    from google.antigravity import LocalOpenAIAgentConfig

    config = AntigravityAgentConfig(
        model="llama3",
        base_url="http://localhost:11434/v1",
        openai_compatible=True,
        save_dir=str(tmp_path / "saves"),
    )
    agent = config.get_agent()
    sdk_cfg = await agent._build_sdk_config()
    assert isinstance(sdk_cfg, LocalOpenAIAgentConfig)
    assert sdk_cfg.base_url == "http://localhost:11434/v1"
    assert sdk_cfg.model == "llama3"


async def test_build_sdk_config_interactive_enables_ask_question(tmp_path) -> None:
    from google.antigravity import BuiltinTools

    config = AntigravityAgentConfig(
        api_key="test-key",
        behavior="interactive",
        save_dir=str(tmp_path / "saves"),
    )
    agent = config.get_agent()
    sdk_cfg = await agent._build_sdk_config()
    assert sdk_cfg.capabilities.enabled_tools is not None
    assert BuiltinTools.ASK_QUESTION in sdk_cfg.capabilities.enabled_tools
