"""AgentPool: main package.

Pydantic-AI based Multi-Agent Framework with YAML-based Agents, Teams, Workflows &
Extended ACP / AGUI integration.
"""

from __future__ import annotations

from importlib.metadata import version
from upathtools import register_http_filesystems

from agentpool.models.agents import NativeAgentConfig
from agentpool.models.manifest import AgentsManifest

# Builtin toolsets imports removed to avoid circular dependency
# Import them directly from agentpool_toolsets.builtin when needed
from agentpool.agents import Agent, AgentContext, ClaudeCodeAgent, ACPAgent, AGUIAgent
from agentpool.delegation import AgentPool, Team, TeamRun, BaseTeam
from agentpool.messaging.messages import ChatMessage
from agentpool.tools import Tool, ToolCallInfo, FunctionTool
from agentpool.messaging.messagenode import MessageNode
from agentpool.testing import acp_test_session
from pydantic_ai import (
    AudioUrl,
    BinaryContent,
    BinaryImage,
    DocumentUrl,
    ImageUrl,
    VideoUrl,
)

__version__ = version("agentpool")
__title__ = "AgentPool"
__author__ = "Philipp Temminghoff"
__author_email__ = "philipptemminghoff@googlemail.com"
__copyright__ = "Copyright (c) 2025 Philipp Temminghoff"
__license__ = "MIT"
__url__ = "https://github.com/phil65/agentpool"

register_http_filesystems()

__all__ = [
    "ACPAgent",
    "AGUIAgent",
    "Agent",
    "AgentContext",
    "AgentPool",
    "AgentsManifest",
    "AudioUrl",
    "BaseTeam",
    "BinaryContent",
    "BinaryImage",
    "ChatMessage",
    "ClaudeCodeAgent",
    "DocumentUrl",
    "FunctionTool",
    "ImageUrl",
    "MessageNode",
    "NativeAgentConfig",
    "Team",
    "TeamRun",
    "Tool",
    "ToolCallInfo",
    "VideoUrl",
    "__version__",
    "acp_test_session",
]
