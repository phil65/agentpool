"""Composio based toolset implementation."""

import os
from typing import TYPE_CHECKING, Any, cast

from schemez import OpenAIFunctionDefinition

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from agentpool.tools.base import Tool


logger = get_logger(__name__)


class ComposioTools(ResourceProvider):
    """Provider for composio tools."""

    def __init__(self, user_id: str, toolsets: list[str], api_key: str | None = None) -> None:
        from composio import Composio

        super().__init__(name=f"composio_{user_id}")
        self.user_id = user_id
        key = api_key or os.environ.get("COMPOSIO_API_KEY")
        if key:
            self.composio = Composio(api_key=key)
        else:
            self.composio = Composio()
        self._tools: list[Tool] | None = None
        self._toolkits = toolsets

    def _create_tool_handler(self, tool_slug: str) -> Callable[..., Any]:
        """Create a handler function for a specific tool."""

        def handle_tool_call(**kwargs: Any) -> Any:
            try:
                return self.composio.tools.execute(
                    slug=tool_slug,
                    arguments=kwargs,
                    user_id=self.user_id,
                )
            except Exception:
                logger.exception("Error executing tool", name=tool_slug)
                return {"error": f"Failed to execute tool {tool_slug}"}

        handle_tool_call.__name__ = tool_slug
        return handle_tool_call

    async def get_tools(self) -> Sequence[Tool]:
        """Get tools from composio."""
        # Return cached tools if available
        if self._tools is not None:
            return self._tools

        self._tools = []

        try:
            # Get tools for GitHub toolkit using v3 API
            tools = self.composio.tools.get(self.user_id, toolkits=self._toolkits)

            for tool_def in tools:
                schema = cast(OpenAIFunctionDefinition, tool_def["function"])
                tool_slug = schema.get("name", "")
                if tool_slug:
                    fn = self._create_tool_handler(tool_slug)
                    tool = self.create_tool(fn, schema_override=schema)
                    self._tools.append(tool)

        except Exception:
            logger.exception("Error getting Composio tools")
            # Return empty list if there's an error
            self._tools = []

        return self._tools


if __name__ == "__main__":
    import anyio

    async def main() -> None:
        from agentpool import Agent

        tools = ComposioTools("user@example.com", toolsets=["github"])
        agent = Agent(model="gpt-6-luna")
        agent.tools.add_provider(tools)
        result = await agent.run("tell me the tools at your disposal")
        print(result)

    anyio.run(main)
