"""Package resources for AgentPool configuration."""

from __future__ import annotations

import importlib.resources
from typing import Final

_RESOURCES = importlib.resources.files("agentpool.config_resources")

# Template configuration
AGENTS_TEMPLATE: Final[str] = str(_RESOURCES / "agents_template.yml")
"""Path to the agents template configuration."""

# Pool configurations
ACP_ASSISTANT: Final[str] = str(_RESOURCES / "acp_assistant.yml")
"""Path to default ACP assistant configuration."""

AGENTS: Final[str] = str(_RESOURCES / "agents.yml")
"""Path to the main agents configuration."""

AGUI_TEST: Final[str] = str(_RESOURCES / "agui_test.yml")
"""Path to AGUI test configuration."""

ANTIGRAVITY_ASSISTANT: Final[str] = str(_RESOURCES / "antigravity_agent.yml")
"""Path to default Antigravity assistant configuration."""

CLAUDE_CODE_ASSISTANT: Final[str] = str(_RESOURCES / "claude_code_agent.yml")
"""Path to default Claude code assistant configuration."""

CODEX_ASSISTANT: Final[str] = str(_RESOURCES / "codex_agent.yml")
"""Path to default Codex assistant configuration."""

EXTERNAL_ACP_AGENTS: Final[str] = str(_RESOURCES / "external_acp_agents.yml")
"""Path to external ACP agents configuration."""

TTS_TEST_AGENTS: Final[str] = str(_RESOURCES / "tts_test_agents.yml")
"""Path to TTS test agents configuration."""

# All pool configuration paths for validation
ALL_POOL_CONFIGS: Final[tuple[str, ...]] = (
    ACP_ASSISTANT,
    AGENTS,
    AGUI_TEST,
    ANTIGRAVITY_ASSISTANT,
    CLAUDE_CODE_ASSISTANT,
    CODEX_ASSISTANT,
    EXTERNAL_ACP_AGENTS,
    TTS_TEST_AGENTS,
)
"""All pool configuration file paths."""

__all__ = [
    "ACP_ASSISTANT",
    "AGENTS",
    "AGENTS_TEMPLATE",
    "AGUI_TEST",
    "ALL_POOL_CONFIGS",
    "ANTIGRAVITY_ASSISTANT",
    "CLAUDE_CODE_ASSISTANT",
    "CODEX_ASSISTANT",
    "EXTERNAL_ACP_AGENTS",
    "TTS_TEST_AGENTS",
]
