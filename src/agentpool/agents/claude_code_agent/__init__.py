"""Claude Code Agent - Native Claude Agent SDK integration.

This module provides an agent implementation that wraps the Claude Agent SDK's
ClaudeSDKClient for native integration with agentpool.
"""

from agentpool.agents.claude_code_agent.claude_code_agent import ClaudeCodeAgent
from agentpool.agents.claude_code_agent.hook_manager import ClaudeCodeHookManager


__all__ = ["ClaudeCodeAgent", "ClaudeCodeHookManager"]
