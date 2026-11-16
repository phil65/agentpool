"""Command utilities."""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Literal
import webbrowser

from slashed import CommandContext, CommandError

from agentpool.messaging.context import NodeContext  # noqa: TC001
from agentpool_commands.base import NodeCommand


if TYPE_CHECKING:
    from agentpool_commands.text_sharing import TextSharerStr, Visibility


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class CopyClipboardCommand(NodeCommand):
    """Copy messages from conversation history to system clipboard.

    Allows copying a configurable number of messages with options for:
    - Number of messages to include
    - Token limit for context size
    - Custom format templates

    Requires copykitten package to be installed.
    """

    name = "copy-clipboard"
    category = "utils"

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext],
        *,
        num_messages: int = 1,
        max_tokens: int | None = None,
        format_template: str | None = None,
    ) -> None:
        """Copy messages to clipboard.

        Args:
            ctx: Command context
            num_messages: Number of messages to copy (default: 1)
            max_tokens: Only include messages up to token limit
            format_template: Custom format template
        """
        try:
            import copykitten
        except ImportError as e:
            raise CommandError("copykitten package required for clipboard operations") from e

        content = await ctx.context.agent.conversation.format_history(
            num_messages=num_messages,
            max_tokens=max_tokens,
            format_template=format_template,
        )

        if not content.strip():
            await ctx.print("ℹ️ **No messages found to copy**")  #  noqa: RUF001
            return

        try:
            copykitten.copy(content)
            await ctx.print("📋 **Messages copied to clipboard**")
        except Exception as e:
            raise CommandError(f"Failed to copy to clipboard: {e}") from e

    @classmethod
    def condition(cls) -> bool:
        """Check if copykitten is available."""
        return importlib.util.find_spec("copykitten") is not None


class EditAgentFileCommand(NodeCommand):
    """Open the agent's configuration file in your default editor.

    This file contains:
    - Agent settings and capabilities
    - System promptss
    - Model configuration
    - Environment references
    - Role definitions

    Note: Changes to the configuration file require reloading the agent.
    """

    name = "open-agent-file"
    category = "utils"

    async def execute_command(self, ctx: CommandContext[NodeContext]) -> None:
        """Open agent's configuration file."""
        agent = ctx.context.agent
        if not agent.agent_pool:
            raise RuntimeError("Agent is not part of an agent pool")
        config_file_path = agent.agent_pool.manifest.config_file_path
        if not config_file_path:
            raise CommandError("No configuration file path available")

        try:
            webbrowser.open(config_file_path)
            msg = f"🌐 **Opening agent configuration:** `{config_file_path}`"
            await ctx.print(msg)
        except Exception as e:
            raise CommandError(f"Failed to open configuration file: {e}") from e


class ShareHistoryCommand(NodeCommand):
    """Share message history using various text sharing providers.

    Supports multiple providers:
    - gist: GitHub Gist (requires GITHUB_TOKEN or GH_TOKEN)
    - pastebin: Pastebin (requires PASTEBIN_API_KEY)
    - paste_rs: paste.rs (no authentication required)
    - opencode: OpenCode (no authentication required)

    The shared content can be conversation history or custom text.
    """

    name = "share-text"
    category = "utils"

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext],
        *,
        provider: TextSharerStr = "paste_rs",
        num_messages: int | None = None,
        max_tokens: int | None = None,
        format_template: str | None = None,
        title: str | None = None,
        syntax: str = "markdown",
        visibility: Visibility = "unlisted",
    ) -> None:
        """Share text content via a text sharing provider.

        Args:
            ctx: Command context
            provider: Text sharing provider to use
            num_messages: Number of messages from history to share (None = all messages)
            max_tokens: Token limit for conversation history
            format_template: Custom format template for history
            title: Title/filename for the shared content
            syntax: Syntax highlighting (e.g., "python", "markdown")
            visibility: Visibility level
        """
        from agentpool_commands.text_sharing import get_sharer

        conversation = ctx.context.agent.conversation

        # Check if there's content to share
        messages_to_check = (
            conversation.chat_messages[-num_messages:]
            if num_messages
            else conversation.chat_messages
        )
        if not messages_to_check:
            await ctx.print("ℹ️ **No content to share**")  # noqa: RUF001
            return

        try:
            # Get the appropriate sharer
            sharer = get_sharer(provider)

            # Use structured sharing for providers that support it (OpenCode)
            # Otherwise fall back to plain text sharing
            result = await sharer.share_conversation(
                conversation,
                title=title,
                visibility=visibility,
                num_messages=num_messages,
            )
            # Format success message
            provider_name = sharer.name
            msg_parts = [f"🔗 **Content shared via {provider_name}:**", f"• URL: {result.url}"]
            if result.raw_url:
                msg_parts.append(f"• Raw: {result.raw_url}")
            if result.delete_url:
                msg_parts.append(f"• Delete: {result.delete_url}")

            await ctx.print("\n".join(msg_parts))

        except Exception as e:
            raise CommandError(f"Failed to share content via {provider}: {e}") from e


class GetLogsCommand(NodeCommand):
    """Get recent log entries from memory.

    Shows captured log entries with filtering options:
    - Filter by minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - Filter by logger name substring
    - Limit number of entries returned

    Logs are shown newest first.
    """

    name = "get-logs"
    category = "debug"

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext],
        *,
        level: LogLevel = "INFO",
        logger_filter: str | None = None,
        limit: int | str = 50,
    ) -> None:
        """Get recent log entries.

        Args:
            ctx: Command context
            level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            logger_filter: Only show logs from loggers containing this string
            limit: Maximum number of log entries to return
        """
        from agentpool_toolsets.builtin.debug import get_memory_handler

        # Convert limit to int (slashed passes args as strings)
        limit_int = int(limit) if isinstance(limit, str) else limit

        handler = get_memory_handler()
        records = handler.get_records(level=level, logger_filter=logger_filter, limit=limit_int)

        if not records:
            await ctx.print("No log entries found matching criteria")
            return

        await ctx.print(f"## 📋 {len(records)} Log Entries (newest first)\n")
        for record in records:
            await ctx.print(record.message)
