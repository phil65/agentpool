"""Search toolset implementation using searchly providers."""

from typing import TYPE_CHECKING, Any

from searchly.base import CountryCode, LanguageCode  # noqa: TC002

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence

    from searchly.base import (
        NewsSearchProvider,
        NewsSearchResponse,
        WebSearchProvider,
        WebSearchResponse,
    )

    from agentpool.agents.context import AgentContext
    from agentpool.tools.base import Tool


logger = get_logger(__name__)


def format_web_results(response: WebSearchResponse, query: str) -> str:
    """Format web search results as markdown."""
    if not response.results:
        return f"No results found for: {query}"

    lines = [f"## Web Search Results for: {query}\n"]
    for i, result in enumerate(response.results, 1):
        lines.append(f"### {i}. [{result.title}]({result.url})")
        lines.append(f"{result.snippet}\n")
    return "\n".join(lines)


def format_news_results(response: NewsSearchResponse, query: str) -> str:
    """Format news search results as markdown."""
    if not response.results:
        return f"No news results found for: {query}"

    lines = [f"## News Search Results for: {query}\n"]
    for i, result in enumerate(response.results, 1):
        # Build header with source/date if available
        header = f"### {i}. [{result.title}]({result.url})"
        lines.append(header)

        meta_parts = []
        if result.source:
            meta_parts.append(f"*{result.source}*")
        if result.published:
            meta_parts.append(f"({result.published})")
        if meta_parts:
            lines.append(" ".join(meta_parts))

        lines.append(f"{result.snippet}\n")
    return "\n".join(lines)


class SearchTools(ResourceProvider):
    """Provider for web and news search tools."""

    def __init__(
        self,
        web_search: WebSearchProvider | None = None,
        news_search: NewsSearchProvider | None = None,
    ) -> None:
        """Initialize search tools provider.

        Args:
            web_search: Web search provider instance.
            news_search: News search provider instance.
        """
        super().__init__(name="search")
        self._web_provider = web_search
        self._news_provider = news_search

    async def web_search(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        query: str,
        *,
        max_results: int = 10,
        country: CountryCode | None = None,
        language: LanguageCode | None = None,
        **kwargs: Any,
    ) -> str:
        """Search the web for information.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return.
            country: Country code for regional results.
            language: Language code for results.
            **kwargs: Provider-specific options.

        Returns:
            Formatted markdown search results.
        """
        from agentpool.agents.events import TextContentItem

        assert self._web_provider is not None
        await agent_ctx.events.tool_call_progress(
            title=f"Searching: {query}",
            items=[TextContentItem(text=f"Searching web for: {query}")],
        )

        response = await self._web_provider.web_search(
            query,
            max_results=max_results,
            country=country,
            language=language,
            **kwargs,
        )

        result_count = len(response.results)
        formatted = format_web_results(response, query)

        await agent_ctx.events.tool_call_progress(
            title=f"Found {result_count} results",
            items=[TextContentItem(text=formatted)],
            replace_content=True,
        )

        return formatted

    async def news_search(  # noqa: D417
        self,
        agent_ctx: AgentContext,
        query: str,
        *,
        max_results: int = 10,
        country: CountryCode | None = None,
        language: LanguageCode | None = None,
        **kwargs: Any,
    ) -> str:
        """Search for news articles.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return.
            country: Country code for regional results.
            language: Language code for results.
            **kwargs: Provider-specific options.

        Returns:
            Formatted markdown news results.
        """
        from agentpool.agents.events import TextContentItem

        assert self._news_provider is not None

        await agent_ctx.events.tool_call_progress(
            title=f"Searching news: {query}",
            items=[TextContentItem(text=f"Searching news for: {query}")],
        )

        response = await self._news_provider.news_search(
            query,
            max_results=max_results,
            country=country,
            language=language,
            **kwargs,
        )

        result_count = len(response.results)
        formatted = format_news_results(response, query)

        await agent_ctx.events.tool_call_progress(
            title=f"Found {result_count} articles",
            items=[TextContentItem(text=formatted)],
            replace_content=True,
        )

        return formatted

    async def get_tools(self) -> Sequence[Tool]:
        """Get search tools from configured providers."""
        tools: list[Tool] = []
        if self._web_provider:
            tools.append(
                self.create_tool(self.web_search, read_only=True, idempotent=True, open_world=True)
            )
        if self._news_provider:
            tools.append(
                self.create_tool(self.news_search, read_only=True, idempotent=True, open_world=True)
            )
        return tools
