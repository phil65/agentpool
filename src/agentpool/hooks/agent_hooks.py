"""AgentHooks - Runtime hook container for agent lifecycle events."""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentpool.hooks.base import HookInput, HookResult
from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Sequence

    from exxec import ExecutionEnvironment

    from agentpool.hooks.base import Hook


logger = get_logger(__name__)


@dataclass
class AgentHooks:
    """Runtime container for agent lifecycle hooks.

    Holds instantiated hooks organized by event type and provides
    methods to execute them with proper input/output handling.

    Attributes:
        pre_run: Hooks executed before agent.run() processes a prompt.
        post_run: Hooks executed after agent.run() completes.
        pre_tool_use: Hooks executed before a tool is called.
        post_tool_use: Hooks executed after a tool completes.
    """

    pre_run: Sequence[Hook] = field(default_factory=list)
    post_run: Sequence[Hook] = field(default_factory=list)
    pre_tool_use: Sequence[Hook] = field(default_factory=list)
    post_tool_use: Sequence[Hook] = field(default_factory=list)

    def has_hooks(self) -> bool:
        """Check if any hooks are configured."""
        return bool(self.pre_run or self.post_run or self.pre_tool_use or self.post_tool_use)

    async def run_pre_run_hooks(
        self,
        *,
        agent_name: str,
        prompt: str,
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute pre-run hooks.

        Args:
            agent_name: Name of the agent.
            prompt: The prompt being processed.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.

        Returns:
            Combined hook result. If any hook denies, the run should be blocked.
        """
        input_data = HookInput(
            event="pre_run",
            agent_name=agent_name,
            prompt=prompt,
            session_id=session_id,
        )
        return await self._run_hooks(self.pre_run, input_data, env=env)

    async def run_post_run_hooks(
        self,
        *,
        agent_name: str,
        prompt: str,
        result: Any,
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute post-run hooks.

        Args:
            agent_name: Name of the agent.
            prompt: The prompt that was processed.
            result: The result from the run.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.

        Returns:
            Combined hook result.
        """
        input_data = HookInput(
            event="post_run",
            agent_name=agent_name,
            prompt=prompt,
            result=result,
            session_id=session_id,
        )
        return await self._run_hooks(self.post_run, input_data, env=env)

    async def run_pre_tool_hooks(
        self,
        *,
        agent_name: str,
        tool_name: str,
        tool_input: dict[str, Any],
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute pre-tool-use hooks.

        Args:
            agent_name: Name of the agent.
            tool_name: Name of the tool being called.
            tool_input: Input arguments for the tool.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.

        Returns:
            Combined hook result. If any hook denies, the tool call should be blocked.
            May include modified_input to change tool arguments.
        """
        input_data = HookInput(
            event="pre_tool_use",
            agent_name=agent_name,
            tool_name=tool_name,
            tool_input=tool_input,
            session_id=session_id,
        )
        return await self._run_hooks(self.pre_tool_use, input_data, env=env)

    async def run_post_tool_hooks(
        self,
        *,
        agent_name: str,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: Any,
        duration_ms: float,
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute post-tool-use hooks.

        Args:
            agent_name: Name of the agent.
            tool_name: Name of the tool that was called.
            tool_input: Input arguments that were passed to the tool.
            tool_output: Output from the tool.
            duration_ms: How long the tool took to execute.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.

        Returns:
            Combined hook result. May include additional_context to inject.
        """
        input_data = HookInput(
            event="post_tool_use",
            agent_name=agent_name,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            duration_ms=duration_ms,
            session_id=session_id,
        )
        return await self._run_hooks(self.post_tool_use, input_data, env=env)

    @staticmethod
    async def _run_hooks(
        hooks: Sequence[Hook],
        input_data: HookInput,
        *,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Run a list of hooks and combine their results.

        Hooks are run in parallel. Results are combined:
        - If any hook returns "deny", the combined result is "deny"
        - If any hook returns "ask", the combined result is "ask" (unless denied)
        - Reasons are concatenated
        - modified_input values are merged (later hooks override earlier)
        - additional_context values are concatenated
        - continue_ is False if any hook sets it False

        Args:
            hooks: List of hooks to execute.
            input_data: Input data for the hooks.
            env: Agent's execution environment, passed through to hooks.

        Returns:
            Combined hook result.
        """
        if not hooks:
            return HookResult(decision="allow")

        # Filter to matching hooks
        matching = [h for h in hooks if h.matches(input_data)]
        if not matching:
            return HookResult(decision="allow")

        # Run all matching hooks in parallel
        raw_results = await asyncio.gather(
            *(hook.execute(input_data, env=env) for hook in matching),
            return_exceptions=True,
        )

        # Combine results
        combined = HookResult(decision="allow")
        reasons: list[str] = []
        contexts: list[str] = []

        for raw_result in raw_results:
            if isinstance(raw_result, BaseException):
                logger.warning("Hook execution failed", error=str(raw_result))
                continue

            result: HookResult = raw_result

            # Decision priority: deny > ask > allow
            if result.get("decision") == "deny":
                combined["decision"] = "deny"
            elif result.get("decision") == "ask" and combined.get("decision") != "deny":
                combined["decision"] = "ask"

            # Collect reasons
            if reason := result.get("reason"):
                reasons.append(reason)

            # Merge modified_input (later overrides earlier)
            if modified := result.get("modified_input"):
                if "modified_input" not in combined:
                    combined["modified_input"] = {}
                combined["modified_input"].update(modified)

            # Collect additional context
            if ctx := result.get("additional_context"):
                contexts.append(ctx)

            # continue_ is False if any hook sets it False
            if result.get("continue_") is False:
                combined["continue_"] = False

        # Combine collected values
        if reasons:
            combined["reason"] = "; ".join(reasons)
        if contexts:
            combined["additional_context"] = "\n".join(contexts)

        return combined

    def __repr__(self) -> str:
        counts = {
            "pre_run": len(self.pre_run),
            "post_run": len(self.post_run),
            "pre_tool_use": len(self.pre_tool_use),
            "post_tool_use": len(self.post_tool_use),
        }
        non_empty = {k: v for k, v in counts.items() if v > 0}
        if not non_empty:
            return "AgentHooks(empty)"
        parts = ", ".join(f"{k}={v}" for k, v in non_empty.items())
        return f"AgentHooks({parts})"
