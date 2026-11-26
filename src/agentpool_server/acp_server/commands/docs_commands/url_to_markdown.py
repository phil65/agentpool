"""Convert a webpage to markdown using urltomarkdown.herokuapp.com."""

from __future__ import annotations

import urllib.parse
import uuid

import anyenv
from pydantic_ai import UserPromptPart
from slashed import CommandContext  # noqa: TC002

from agentpool.log import get_logger
from agentpool.messaging.context import NodeContext  # noqa: TC001
from agentpool_commands.base import NodeCommand
from agentpool_server.acp_server.session import ACPSession  # noqa: TC001


logger = get_logger(__name__)


class UrlToMarkdownCommand(NodeCommand):
    """Convert a webpage to markdown using urltomarkdown.herokuapp.com.

    Fetches a web page and converts it to markdown format,
    making it ideal for staging as AI context.
    """

    name = "url-to-markdown"
    category = "docs"

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext[ACPSession]],
        url: str,
        *,
        title: bool = True,
        links: bool = True,
        clean: bool = True,
    ) -> None:
        """Convert a webpage to markdown.

        Args:
            ctx: Command context with ACP session
            url: URL to convert to markdown
            title: Include page title as H1 header
            links: Include links in markdown output
            clean: Clean/filter content before conversion
        """
        session = ctx.context.data
        assert session
        tool_call_id = f"url-to-markdown-{uuid.uuid4().hex[:8]}"
        api_url = "https://urltomarkdown.herokuapp.com/"  # Build API URL and parameters
        params = {"url": url}
        if title:
            params["title"] = "true"
        if not links:
            params["links"] = "false"
        if not clean:
            params["clean"] = "false"
        try:
            await session.output.command_started(
                call_id=tool_call_id,
                title=f"Converting to markdown: {url}",
                kind="fetch",
            )
            response = await anyenv.get(api_url, params=params, timeout=30.0)
            markdown_content = response.text
            page_title = ""
            # Header names are lower-case in response.headers.
            if x_title := response.headers.get("x-title"):
                page_title = f" - {urllib.parse.unquote(x_title)}"
            # Stage the markdown content for use in agent context
            content = f"Webpage content from {url}{page_title}:\n\n{markdown_content}"
            staged_part = UserPromptPart(content=content)
            ctx.context.agent.staged_content.add([staged_part])
            # Send successful result - wrap in code block for proper display
            staged_count = len(ctx.context.agent.staged_content)
            await session.output.command_updated(
                call_id=tool_call_id,
                status="completed",
                title=f"Webpage converted and staged ({staged_count} total parts)",
                text=f"```markdown\n{markdown_content}\n```",
            )
        except Exception as e:
            logger.exception("Unexpected error converting URL", url=url)
            await session.output.command_updated(
                call_id=tool_call_id,
                status="failed",
                title=f"Error: {e}",
            )
