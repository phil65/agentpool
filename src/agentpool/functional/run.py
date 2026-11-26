"""Functional wrappers for Agent usage."""

from typing import TYPE_CHECKING, Any, Unpack, overload

from anyenv import run_sync
from pydantic_ai import ImageUrl

from agentpool import Agent


if TYPE_CHECKING:
    from agentpool.agents.native_agent import AgentKwargs
    from agentpool.common_types import PromptCompatible


@overload
async def run_agent[TResult](
    prompt: PromptCompatible,
    image_url: str | None = None,
    *,
    output_type: type[TResult],
    **kwargs: Unpack[AgentKwargs],
) -> TResult: ...


@overload
async def run_agent(
    prompt: PromptCompatible,
    image_url: str | None = None,
    **kwargs: Unpack[AgentKwargs],
) -> str: ...


async def run_agent(
    prompt: PromptCompatible,
    image_url: str | None = None,
    *,
    output_type: type[Any] | None = None,
    **kwargs: Unpack[AgentKwargs],
) -> Any:
    """Run prompt through agent and return result."""
    async with Agent[Any, str](**kwargs) as agent:
        # Convert to structured output agent if output_type specified
        final = agent.to_structured(output_type) if output_type is not None else agent

        if image_url:
            image = ImageUrl(url=image_url)
            result = await final.run(prompt, image)
        else:
            result = await final.run(prompt)
        return result.content


@overload
def run_agent_sync[TResult](
    prompt: PromptCompatible,
    image_url: str | None = None,
    *,
    output_type: type[TResult],
    **kwargs: Unpack[AgentKwargs],
) -> TResult: ...


@overload
def run_agent_sync(
    prompt: PromptCompatible,
    image_url: str | None = None,
    **kwargs: Unpack[AgentKwargs],
) -> str: ...


def run_agent_sync(
    prompt: PromptCompatible,
    image_url: str | None = None,
    *,
    output_type: type[Any] | None = None,
    **kwargs: Unpack[AgentKwargs],
) -> Any:
    """Sync wrapper for run_agent."""

    async def _run() -> Any:
        return await run_agent(prompt, image_url, output_type=output_type, **kwargs)  # type: ignore[arg-type]

    return run_sync(_run())
