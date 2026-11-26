"""Serve the pool's prompt library as MCP prompts."""

from typing import TYPE_CHECKING

from fastmcp.prompts.base import Prompt
from fastmcp.server.providers.base import Provider

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from agentpool.prompts.manager import PromptManager


logger = get_logger(__name__)

BUILTIN = "builtin"


def prompt_name(provider: str, identifier: str) -> str:
    """The MCP name of a prompt: its own for the library's, ``<provider>.<name>`` for a hub's."""
    return identifier if provider == BUILTIN else f"{provider}.{identifier}"


class PoolPromptsProvider(Provider):
    """FastMCP prompt provider serving a pool's prompt library.

    The library's own prompts (``prompts.system_prompts``) keep their names; prompts of
    prompt hubs (``prompts.providers``) are served as ``<provider>.<name>``. Prompts are
    listed when a client asks, and fetched when it gets one, so hub changes show up.
    """

    def __init__(self, prompts: PromptManager, names: Sequence[str] | None = None) -> None:
        """Initialize the provider.

        Args:
            prompts: The pool's prompt manager
            names: Only these prompts (MCP names); all without
        """
        super().__init__()
        self._prompts = prompts
        self._names = set(names) if names is not None else None

    async def _list_prompts(self) -> Sequence[Prompt]:
        builtin = self._prompts.get_builtin_prompts()
        served: list[Prompt] = []
        for provider, identifiers in (await self._prompts.list_prompts()).items():
            for identifier in identifiers:
                name = prompt_name(provider, identifier)
                if self._names is not None and name not in self._names:
                    continue
                if provider == BUILTIN and (config := builtin.get(identifier)) is not None:
                    description = f"{config.category.capitalize()} prompt from the pool's library"
                else:
                    description = f"Prompt {identifier!r} from the {provider} prompt hub"
                render = self._render(provider, identifier)
                served.append(Prompt.from_function(render, name=name, description=description))
        return served

    def _render(self, provider: str, identifier: str) -> Callable[[], Awaitable[str]]:
        async def render() -> str:
            hub = None if provider == BUILTIN else provider
            return await self._prompts.get_from(identifier, provider=hub)

        return render
