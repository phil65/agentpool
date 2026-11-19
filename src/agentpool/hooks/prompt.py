"""Prompt-based hook implementation."""

from __future__ import annotations

from string import Template
from typing import TYPE_CHECKING, Literal

import anyenv
from pydantic import Field
from schemez import Schema

from agentpool.hooks.base import Hook, HookResult
from agentpool.log import get_logger


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

    from agentpool.hooks.base import HookEvent, HookInput


logger = get_logger(__name__)

# Default fast model for hook evaluation
DEFAULT_HOOK_MODEL = "openai:gpt-4o-mini"


class HookDecision(Schema):
    """Structured response from LLM hook evaluation."""

    decision: Literal["allow", "deny"]
    """Whether to allow or deny the action"""

    reason: str = Field(default="")
    """Explanation for the decision"""


class PromptHook(Hook):
    """Hook that uses an LLM to evaluate the action.

    The prompt is sent to a fast LLM which returns a structured decision.
    Supports placeholders: $INPUT, $TOOL_NAME, $TOOL_INPUT, $AGENT_NAME, etc.
    """

    def __init__(
        self,
        event: HookEvent,
        prompt: str,
        matcher: str | None = None,
        timeout: float = 30.0,
        enabled: bool = True,
        model: str | None = None,
    ) -> None:
        """Initialize prompt hook.

        Args:
            event: The lifecycle event this hook handles.
            prompt: Prompt template for LLM evaluation.
            matcher: Regex pattern for matching.
            timeout: Maximum execution time in seconds.
            enabled: Whether this hook is active.
            model: Model to use for evaluation. Defaults to a fast model.
        """
        super().__init__(event=event, matcher=matcher, timeout=timeout, enabled=enabled)
        self.prompt_template = prompt
        self.model = model or DEFAULT_HOOK_MODEL

    async def execute(
        self,
        input_data: HookInput,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute the LLM evaluation.

        Args:
            input_data: The hook input data.
            env: Unused. Prompt hooks always run locally via LLM call.

        Returns:
            Hook result from LLM.
        """
        try:
            prompt = self._build_prompt(input_data)
            decision = await self._query_llm(prompt)
            return HookResult(decision=decision.decision, reason=decision.reason)
        except Exception as e:
            logger.exception("Prompt hook failed")
            return HookResult(decision="allow", reason=str(e))

    def _build_prompt(self, input_data: HookInput) -> str:
        """Build prompt with placeholder substitutions.

        Args:
            input_data: The hook input data.

        Returns:
            Formatted prompt string.
        """
        variables = {
            "INPUT": anyenv.dump_json(dict(input_data), indent=True),
            "TOOL_NAME": input_data.get("tool_name", ""),
            "TOOL_INPUT": anyenv.dump_json(input_data.get("tool_input", {}), indent=True),
            "TOOL_OUTPUT": anyenv.dump_json(input_data.get("tool_output", ""), indent=True),
            "AGENT_NAME": input_data.get("agent_name", ""),
            "PROMPT": input_data.get("prompt", ""),
            "EVENT": input_data.get("event", ""),
        }
        template = Template(self.prompt_template)
        return template.safe_substitute(variables)

    async def _query_llm(self, prompt: str) -> HookDecision:
        """Query the LLM for evaluation.

        Args:
            prompt: The evaluation prompt.

        Returns:
            Structured hook decision.
        """
        from agentpool import Agent

        agent = Agent(
            model=self.model,
            system_prompt="You are a security/policy evaluation assistant. "
            "Analyze the request and decide whether to allow or deny it.",
            output_type=HookDecision,
        )
        result = await agent.run(prompt)
        return result.data
