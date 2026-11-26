"""FastMCP message handler for agentpool."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, assert_never

from agentpool.log import get_logger


if TYPE_CHECKING:
    from mcp import types

    from agentpool.mcp_server import MCPClient

logger = get_logger(__name__)

MetaDict = dict[str, Any]
ChangeCallback = Callable[[MetaDict], Awaitable[None]]
ProgressCallback = Callable[[float, float | None, str | None, MetaDict], Awaitable[None]]


@dataclass
class MCPMessageHandler:
    """Custom message handler that bridges FastMCP to agentpool notifications.

    Only server notifications and transport-level exceptions reach this handler.
    Server-initiated requests (ping, sampling, roots, elicitation) are answered
    through the client's dedicated callbacks instead.
    """

    client: MCPClient
    """The MCP client instance."""
    tool_change_callback: ChangeCallback | None = None
    """Tool change callback."""
    prompt_change_callback: ChangeCallback | None = None
    """Prompt change callback."""
    resource_change_callback: ChangeCallback | None = None
    """Resource change callback."""
    progress_callback: ProgressCallback | None = None
    """Progress callback: (progress, total, message, meta)."""

    async def __call__(self, message: types.ServerNotification | Exception) -> None:
        """Handle FastMCP messages by dispatching to appropriate handlers."""
        from mcp import types

        await self.on_message(message)
        match message:
            case Exception():
                await self.on_exception(message)
            case _:
                await self.on_notification(message)
                match message:
                    case types.CancelledNotification():
                        await self.on_cancelled(message)
                    case types.ProgressNotification():
                        await self.on_progress(message)
                    case types.LoggingMessageNotification():
                        await self.on_logging_message(message)
                    case types.ToolListChangedNotification():
                        await self.on_tool_list_changed(message)
                    case types.ResourceListChangedNotification():
                        await self.on_resource_list_changed(message)
                    case types.PromptListChangedNotification():
                        await self.on_prompt_list_changed(message)
                    case types.ResourceUpdatedNotification():
                        await self.on_resource_updated(message)
                    case types.ElicitCompleteNotification():
                        await self.on_elicit_complete(message)
                    case types.SubscriptionsAcknowledgedNotification():
                        await self.on_subscriptions_acknowledged(message)
                    case _ as unreachable:
                        assert_never(unreachable)

    async def on_message(self, message: types.ServerNotification | Exception) -> None:
        """Handle generic messages."""

    async def on_notification(self, message: types.ServerNotification) -> None:
        """Handle server notifications."""

    async def on_tool_list_changed(self, message: types.ToolListChangedNotification) -> None:
        """Handle tool list changes."""
        logger.info("MCP tool list changed", message=message)
        # Call the tool change callback if provided
        if self.tool_change_callback:
            dct = (message.params.meta if message.params else None) or {}
            await self.tool_change_callback(dct)

    async def on_resource_list_changed(
        self, message: types.ResourceListChangedNotification
    ) -> None:
        """Handle resource list changes."""
        logger.info("MCP resource list changed", message=message)
        # Call the resource change callback if provided
        if self.resource_change_callback:
            dct = (message.params.meta if message.params else None) or {}
            await self.resource_change_callback(dct)

    async def on_resource_updated(self, message: types.ResourceUpdatedNotification) -> None:
        """Handle resource updates."""
        logger.info("MCP resource updated", uri=message.params.uri)

    async def on_progress(self, message: types.ProgressNotification) -> None:
        """Handle progress notifications with metadata."""
        if self.progress_callback:
            params = message.params
            await self.progress_callback(
                params.progress, params.total, params.message, params.meta or {}
            )

    async def on_prompt_list_changed(self, message: types.PromptListChangedNotification) -> None:
        """Handle prompt list changes."""
        logger.info("MCP prompt list changed", message=message)
        # Call the prompt change callback if provided
        if self.prompt_change_callback:
            dct = (message.params.meta if message.params else None) or {}
            await self.prompt_change_callback(dct)

    async def on_cancelled(self, message: types.CancelledNotification) -> None:
        """Handle cancelled operations."""
        logger.info("MCP operation cancelled", message=message)

    async def on_subscriptions_acknowledged(
        self, message: types.SubscriptionsAcknowledgedNotification
    ) -> None:
        """Handle subscription acknowledgement notifications."""
        logger.debug("MCP subscriptions acknowledged", message=message)

    async def on_logging_message(self, message: types.LoggingMessageNotification) -> None:
        """Handle server log messages."""
        # This is handled by _log_handler, but keep for completeness

    async def on_exception(self, message: Exception) -> None:
        """Handle exceptions."""
        logger.error("MCP client exception", error=message)

    async def on_elicit_complete(self, message: types.ElicitCompleteNotification) -> None:
        """Handle elicitation completion notifications.

        Sent by servers when a URL mode elicitation completes out-of-band.
        """
        elicitation_id = message.params.elicitation_id
        logger.info("MCP elicitation completed", elicitation_id=elicitation_id)
        await self.client.complete_url_elicitation(elicitation_id)
