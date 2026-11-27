"""Core data models for AgentPool."""

__lazy_modules__ = [
    "agentpool.models.acp_agents",
    "agentpool.models.agents",
    "agentpool.models.agui_agents",
    "agentpool.models.antigravity_agents",
    "agentpool.models.claude_code_agents",
    "agentpool.models.manifest",
    "agentpool.models.opencode_v2_agents",
    "agentpool.models.t3_agents",
]

from agentpool.models.acp_agents import ACPAgentConfig, ACPAgentConfigTypes, BaseACPAgentConfig
from agentpool.models.agents import AnyToolConfig, NativeAgentConfig  # noqa: F401
from agentpool.models.agui_agents import AGUIAgentConfig
from agentpool.models.antigravity_agents import AntigravityAgentConfig
from agentpool.models.opencode_v2_agents import OpenCodeV2AgentConfig
from agentpool.models.t3_agents import T3AgentConfig
from agentpool.models.claude_code_agents import ClaudeCodeAgentConfig
from agentpool.models.manifest import AgentsManifest, AnyAgentConfig


__all__ = [
    "ACPAgentConfig",
    "ACPAgentConfigTypes",
    "AGUIAgentConfig",
    "AgentsManifest",
    "AntigravityAgentConfig",
    "AnyAgentConfig",
    "BaseACPAgentConfig",
    "ClaudeCodeAgentConfig",
    "NativeAgentConfig",
    "OpenCodeV2AgentConfig",
    "T3AgentConfig",
]
