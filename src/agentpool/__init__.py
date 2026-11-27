"""AgentPool: main package.

Pydantic-AI based Multi-Agent Framework with YAML-based Agents, Teams, Workflows &
Extended ACP / AGUI integration.
"""

from importlib.metadata import version
import sys
from typing import TYPE_CHECKING, Any

from upathtools import register_http_filesystems

__lazy_modules__ = [
    "agentpool.agents",
    "agentpool.delegation",
    "agentpool.messaging.messages",
    "agentpool.messaging.messagenode",
    "agentpool.models.agents",
    "agentpool.models.manifest",
    "agentpool.testing",
    "agentpool.tools",
    "pydantic_ai",
]


def _light_import() -> bool:
    """Whether this process runs the repomap CLI, which needs none of the framework.

    ``python -m agentpool.repomap`` imports this package first; skipping the eager
    imports below cuts its startup from seconds to a fraction of one. Everything
    else keeps the eager imports (the import order they establish matters).
    """
    argv = sys.orig_argv
    if "-m" in argv:
        index = argv.index("-m")
        return argv[index + 1 : index + 2] == ["agentpool.repomap"]
    return False


if TYPE_CHECKING or not _light_import():
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


def __getattr__(name: str) -> Any:
    """Import the public names on first use (light-import mode only)."""
    if name not in __all__:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    from importlib import import_module

    for module_name in _PUBLIC_MODULES:
        module = import_module(module_name)
        if hasattr(module, name):
            value = getattr(module, name)
            globals()[name] = value
            return value
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


_PUBLIC_MODULES = (
    "agentpool.models.agents",
    "agentpool.models.manifest",
    "agentpool.agents",
    "agentpool.delegation",
    "agentpool.messaging.messages",
    "agentpool.tools",
    "agentpool.messaging.messagenode",
    "agentpool.testing",
    "pydantic_ai",
)

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
