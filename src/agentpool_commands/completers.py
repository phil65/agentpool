"""Command completion providers."""

from typing import TYPE_CHECKING, Any, Literal

from slashed import CompletionItem, CompletionProvider

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.messaging.context import NodeContext  # noqa: TC001


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from slashed import CompletionContext

    from agentpool.agents.base_agent import AgentTypeLiteral


def get_available_agents(
    ctx: CompletionContext[AgentContext[Any]],
    agent_type: AgentTypeLiteral | Literal["all"] = "all",
) -> list[str]:
    """Get available agent names.

    Args:
        ctx: Completion context
        agent_type: Filter by agent type ("native", "acp", "agui", "claude", or "all")
    """
    pool = ctx.command_context.context.pool
    if pool is None:
        return []

    if agent_type == "all":
        return list(pool.all_agents.keys())
    # Filter by AGENT_TYPE attribute
    return [name for name, agent in pool.all_agents.items() if agent_type == agent.AGENT_TYPE]


def get_available_nodes(ctx: CompletionContext[NodeContext[Any]]) -> list[str]:
    """Get available node names."""
    if ctx.command_context.context.pool is None:
        return []
    return list(ctx.command_context.context.pool.nodes.keys())


async def get_model_names(ctx: CompletionContext[AgentContext[Any]]) -> list[str]:
    """Get available model names from pydantic-ai and current configuration.

    Returns:
    - All models from KnownModelName literal type
    - Plus any additional models from current configuration
    """
    return [m.id for m in await ctx.command_context.context.agent.get_available_models() or []]


class PromptCompleter(CompletionProvider):
    """Completer for prompts."""

    async def get_completions(
        self, context: CompletionContext[AgentContext[Any]]
    ) -> AsyncIterator[CompletionItem]:
        """Complete prompt references."""
        current = context.current_word
        pool = context.command_context.context.pool
        if pool is None:
            return

        prompt_manager = pool.prompt_manager
        builtin_prompts = prompt_manager.get_builtin_prompts()

        # If no : yet, suggest providers
        if ":" not in current:
            # Always suggest builtin prompts without prefix
            for name in builtin_prompts:
                if not name.startswith(current):
                    continue
                yield CompletionItem(name, metadata="Builtin prompt", kind="choice")

            # Suggest provider prefixes
            for provider_name in prompt_manager.providers:
                if provider_name == "builtin":
                    continue
                prefix = f"{provider_name}:"
                if not prefix.startswith(current):
                    continue
                yield CompletionItem(prefix, metadata="Prompt provider", kind="choice")
            return

        # If after provider:, get prompts from that provider
        provider_, partial = current.split(":", 1)
        if provider_ == "builtin" or not provider_:
            # Complete from system prompts
            for name in builtin_prompts:
                if not name.startswith(partial):
                    continue
                text = f"{provider_}:{name}" if provider_ else name
                yield CompletionItem(text=text, metadata="Builtin prompt", kind="choice")
