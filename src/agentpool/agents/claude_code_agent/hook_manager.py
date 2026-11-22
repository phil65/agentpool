"""Hook manager for ClaudeCodeAgent.

Centralizes all hook-related logic:
- Built-in hooks (injection via PostToolUse)
- AgentHooks integration
- Injection consumption from PromptInjectionManager
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from clawd_code_sdk.models import (
        HookContext,
        HookEvent,
        HookInput,
        HookMatcher,
        SyncHookJSONOutput,
    )
    from exxec import ExecutionEnvironment

    from agentpool.agents.prompt_injection import PromptInjectionManager
    from agentpool.hooks import AgentHooks

logger = get_logger(__name__)


class ClaudeCodeHookManager:
    """Manages SDK hooks for ClaudeCodeAgent.

    Responsibilities:
    - Builds SDK hooks configuration from multiple sources
    - Consumes injections from PromptInjectionManager
    - Provides clean API for hook-related operations
    """

    def __init__(
        self,
        *,
        agent_name: str,
        agent_hooks: AgentHooks,
        injections: Callable[[], PromptInjectionManager] | None = None,
        set_mode: Callable[[str, str], Awaitable[None]] | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> None:
        """Initialize hook manager.

        Args:
            agent_name: Name of the agent (for logging/events)
            agent_hooks: Optional AgentHooks for pre/post tool hooks
            injections: The injection manager of the turn running, looked up at each
                tool call (the SDK runs hooks outside the turn's context)
            set_mode: Callback to set agent mode (mode_id, category_id)
            env: Agent's execution environment, passed to command hooks
        """
        self.agent_name = agent_name
        self.agent_hooks = agent_hooks
        self._injections = injections
        self._set_mode = set_mode
        self._env = env

    def build_hooks(self) -> dict[HookEvent, list[HookMatcher]]:
        """Build complete SDK hooks configuration.

        Combines:
        - Built-in hooks (injection via PostToolUse)
        - AgentHooks (pre/post tool use)

        Returns:
            Dictionary mapping hook event names to HookMatcher lists
        """
        from clawd_code_sdk.models import HookMatcher

        from agentpool.agents.claude_code_agent.converters import build_sdk_hooks_from_agent_hooks

        result: dict[HookEvent, list[HookMatcher]] = {}
        # Add PostToolUse hook for injection
        result["PostToolUse"] = [HookMatcher(matcher="*", hooks=[self._on_post_tool_use])]
        # Merge AgentHooks if present
        agent_hooks = build_sdk_hooks_from_agent_hooks(self.agent_hooks, self.agent_name, self._env)
        for event_name, matchers in agent_hooks.items():
            if event_name in result:
                result[event_name].extend(matchers)
            else:
                result[event_name] = matchers

        return result

    async def _on_post_tool_use(
        self,
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput:
        """Handle PostToolUse hook for injection and observation.

        Consumes pending injection from the shared PromptInjectionManager
        and adds it as additionalContext in the response.
        """
        from clawd_code_sdk.models import PostToolUseHookSpecificOutput

        result: SyncHookJSONOutput = {"continue_": True}
        # Consume pending injection from shared manager
        tool_name = input_data.get("tool_name", "unknown")
        if self._injections is not None and (injection := await self._injections().consume()):
            logger.debug("Injecting context after tool use", agent=self.agent_name, tool=tool_name)
            result["hookSpecificOutput"] = PostToolUseHookSpecificOutput(
                hookEventName="PostToolUse",
                additionalContext=injection,
            )
        if tool_name == "EnterPlanMode" and self._set_mode:
            await self._set_mode("plan", "mode")

        return result
