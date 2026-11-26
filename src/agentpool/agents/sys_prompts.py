"""System prompt management."""

from functools import cache
from typing import TYPE_CHECKING, Any, Literal

from agentpool import text_templates
from agentpool.agents.exceptions import PromptResolutionError


if TYPE_CHECKING:
    from jinjarope import Environment
    from toprompt import AnyPromptType

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.prompts.manager import PromptManager


ToolInjectionMode = Literal["off", "all"]
ToolUsageStyle = Literal["suggestive", "strict"]


@cache
def get_jinja_env() -> Environment:
    from jinjarope import Environment
    from toprompt import to_prompt

    env = Environment(enable_async=True)
    env.filters["to_prompt"] = to_prompt
    return env


class SystemPrompts:
    """Manages system prompts for an agent."""

    def __init__(
        self,
        prompts: AnyPromptType | list[AnyPromptType] | None = None,
        template: str | None = None,
        dynamic: bool = True,
        prompt_manager: PromptManager | None = None,
        inject_agent_info: bool = True,
        inject_tools: ToolInjectionMode = "off",
        tool_usage_style: ToolUsageStyle = "suggestive",
    ) -> None:
        """Initialize prompt manager."""
        match prompts:
            case list():
                self.prompts: list[AnyPromptType] = prompts
            case None:
                self.prompts = []
            case _:
                self.prompts = [prompts]
        self.prompt_manager = prompt_manager
        self.template = template
        self.dynamic = dynamic
        self.inject_agent_info = inject_agent_info
        self.inject_tools = inject_tools
        self.tool_usage_style = tool_usage_style
        self._cached = False

    def __repr__(self) -> str:
        return (
            f"SystemPrompts(prompts={len(self.prompts)}, "
            f"dynamic={self.dynamic}, inject_agent_info={self.inject_agent_info}, "
            f"inject_tools={self.inject_tools!r})"
        )

    def __len__(self) -> int:
        return len(self.prompts)

    def __getitem__(self, idx: int | slice) -> AnyPromptType | list[AnyPromptType]:
        return self.prompts[idx]

    async def add_by_reference(self, reference: str) -> None:
        """Add a system prompt using reference syntax.

        Args:
            reference: [provider:]identifier[@version][?var1=val1,...]

        Examples:
            await sys_prompts.add_by_reference("code_review?language=python")
            await sys_prompts.add_by_reference("langfuse:expert@v2")
        """
        if not self.prompt_manager:
            raise PromptResolutionError("no prompt_manager available")

        try:
            content = await self.prompt_manager.get(reference)
            self.prompts.append(content)
        except Exception as e:
            raise PromptResolutionError(f"failed to add prompt {reference!r}") from e

    async def add(
        self,
        identifier: str,
        *,
        provider: str | None = None,
        version: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> None:
        """Add a system prompt.

        Args:
            identifier: Prompt identifier/name
            provider: Provider name (None = builtin)
            version: Optional version string
            variables: Optional template variables

        Examples:
            await sys_prompts.add("code_review", variables={"language": "python"})
            await sys_prompts.add("expert", provider="langfuse", version="v2")
        """
        if not self.prompt_manager:
            raise PromptResolutionError("no prompt_manager available")

        try:
            content = await self.prompt_manager.get_from(
                identifier,
                provider=provider,
                version=version,
                variables=variables,
            )
            self.prompts.append(content)
        except Exception as e:
            ref = f"{provider + ':' if provider else ''}{identifier}"
            raise PromptResolutionError(f"failed to add prompt {ref!r}") from e

    def clear(self) -> None:
        """Clear all system prompts."""
        self.prompts = []

    async def refresh_cache(self) -> None:
        """Force re-evaluation of prompts."""
        from toprompt import to_prompt

        self.prompts = [await to_prompt(prompt) for prompt in self.prompts]
        self._cached = True

    async def format_system_prompt(self, agent: BaseAgent[Any, Any]) -> str:
        """Format complete system prompt."""
        if not self.dynamic and not self._cached:
            await self.refresh_cache()
        env = get_jinja_env()
        template = env.from_string(self.template or text_templates.get_system_prompt())
        result = await template.render_async(
            agent=agent,
            prompts=self.prompts,
            dynamic=self.dynamic,
            inject_agent_info=self.inject_agent_info,
            inject_tools=self.inject_tools,
            tool_usage_style=self.tool_usage_style,
        )
        return result.strip()
