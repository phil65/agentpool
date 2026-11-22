"""CLI commands for agentpool."""

from __future__ import annotations

from agentpool.agents.native_agent import Agent
from agentpool.agents.agui_agent import AGUIAgent
from agentpool.agents.antigravity_agent import AntigravityAgent
from agentpool.agents.acp_agent import ACPAgent
from agentpool.agents.capabilities import AgentCapabilities
from agentpool.agents.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.codex_agent import CodexAgent
from agentpool.agents.opencode_v2_agent import OpenCodeV2Agent
from agentpool.agents.t3_agent import T3Agent
from agentpool.agents.events import (
    detailed_print_handler,
    resolve_event_handlers,
    simple_print_handler,
)
from agentpool.agents.context import AgentContext
from agentpool.agents.interactions import Interactions
from agentpool.agents.prompt_injection import PromptInjectionManager
from agentpool.agents.sys_prompts import SystemPrompts


__all__ = [
    "ACPAgent",
    "AGUIAgent",
    "Agent",
    "AgentCapabilities",
    "AgentContext",
    "AntigravityAgent",
    "ClaudeCodeAgent",
    "CodexAgent",
    "Interactions",
    "OpenCodeV2Agent",
    "PromptInjectionManager",
    "SystemPrompts",
    "T3Agent",
    "detailed_print_handler",
    "resolve_event_handlers",
    "simple_print_handler",
]
