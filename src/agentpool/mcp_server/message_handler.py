"""FastMCP message handler for agentpool."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, assert_never

from agentpool.log import get_logger


if TYPE_CHECKING:
    from mcp import types
    from mcp.shared.session import RequestResponder

    from agentpool.mcp_server import MCPClient

logger = get_logger(__name__)

MetaDict = dict[str, Any]
ChangeCallback = Callable[[MetaDict], Awaitable[None]]


@dataclass
class MCPMessageHandler:
    """Custom message handler that bridges FastMCP to agentpool notifications."""

    client: MCPClient
    """The MCP client instance."""
    tool_change_callback: ChangeCallback | None = None
    """Tool change callback."""
    prompt_change_callback: ChangeCallback | None = None
    """Prompt change callback."""
    resource_change_callback: ChangeCallback | None = None
    """Resource change callback."""

    async def __call__(
        self,
        message: RequestResponder[types.ServerRequest, types.ClientResult]
        | types.ServerNotification
        | Exception,
    ) -> None:
        """Handle FastMCP messages by dispatching to appropriate handlers."""
        from mcp import types
        from mcp.shared.session import RequestResponder

        await self.on_message(message)
        match message:
            # requests
            case RequestResponder() as responder:
                await self.on_request(responder)
                # Handle specific requests
                root = responder.request.root
                match root:
                    case types.PingRequest():
                        await self.on_ping(root)
                    case types.ListRootsRequest():
                        await self.on_list_roots(root)
                    case types.CreateMessageRequest():
                        await self.on_create_message(root)
                    case (
                        types.GetTaskRequest()
                        | types.ListTasksRequest()
                        | types.ElicitRequest()
                        | types.GetTaskPayloadRequest()
                        | types.CancelTaskRequest()
                    ):
                        pass
                    case _ as unreachable:
                        assert_never(unreachable)  # ty:ignore[type-assertion-failure]

            case types.ServerNotification() as notification:
                await self.on_notification(notification)
                root = notification.root
                match root:
                    case types.CancelledNotification():
                        await self.on_cancelled(root)
                    case types.ProgressNotification():
                        await self.on_progress(root)
                    case types.LoggingMessageNotification():
                        await self.on_logging_message(root)
                    case types.ToolListChangedNotification():
                        await self.on_tool_list_changed(root)
                    case types.ResourceListChangedNotification():
                        await self.on_resource_list_changed(root)
                    case types.PromptListChangedNotification():
                        await self.on_prompt_list_changed(root)
                    case types.ResourceUpdatedNotification():
                        await self.on_resource_updated(root)
                    case types.ElicitCompleteNotification():
                        await self.on_elicit_complete(root)
                    case types.TaskStatusNotification():
                        await self.on_task_status(root)
                    case _ as unreachable:
                        assert_never(unreachable)  # ty:ignore[type-assertion-failure]

            case Exception():
                await self.on_exception(message)

    async def on_message(
        self,
        message: RequestResponder[types.ServerRequest, types.ClientResult]
        | types.ServerNotification
        | Exception,
    ) -> None:
        """Handle generic messages."""

    async def on_request(
        self, message: RequestResponder[types.ServerRequest, types.ClientResult]
    ) -> None:
        """Handle requests."""

    async def on_notification(self, message: types.ServerNotification) -> None:
        """Handle server notifications."""

    async def on_tool_list_changed(self, message: types.ToolListChangedNotification) -> None:
        """Handle tool list changes."""
        logger.info("MCP tool list changed", message=message)
        # Call the tool change callback if provided
        if self.tool_change_callback:
            meta = message.params.meta if message.params else None
            dct = meta.model_dump() if meta else {}
            await self.tool_change_callback(dct)

    async def on_resource_list_changed(
        self, message: types.ResourceListChangedNotification
    ) -> None:
        """Handle resource list changes."""
        logger.info("MCP resource list changed", message=message)
        # Call the resource change callback if provided
        if self.resource_change_callback:
            meta = message.params.meta if message.params else None
            dct = meta.model_dump() if meta else {}
            await self.resource_change_callback(dct)

    async def on_resource_updated(self, message: types.ResourceUpdatedNotification) -> None:
        """Handle resource updates."""
        # ResourceUpdatedNotification has uri directly, not in params
        logger.info("MCP resource updated", uri=getattr(message, "uri", "unknown"))

    async def on_progress(self, message: types.ProgressNotification) -> None:
        """Handle progress notifications with proper context."""
        # Note: Progress notifications from MCP servers are now handled per-tool-call
        # with the contextual progress handler, so global notifications are ignored

    async def on_prompt_list_changed(self, message: types.PromptListChangedNotification) -> None:
        """Handle prompt list changes."""
        logger.info("MCP prompt list changed", message=message)
        # Call the prompt change callback if provided
        if self.prompt_change_callback:
            meta = message.params.meta if message.params else None
            dct = meta.model_dump() if meta else {}
            await self.prompt_change_callback(dct)

    async def on_cancelled(self, message: types.CancelledNotification) -> None:
        """Handle cancelled operations."""
        logger.info("MCP operation cancelled", message=message)

    async def on_task_status(self, message: types.TaskStatusNotification) -> None:
        """Handle task status notifications."""
        logger.info("MCP task status", message=message)

    async def on_logging_message(self, message: types.LoggingMessageNotification) -> None:
        """Handle server log messages."""
        # This is handled by _log_handler, but keep for completeness

    async def on_exception(self, message: Exception) -> None:
        """Handle exceptions."""
        logger.error("MCP client exception", error=message)

    async def on_ping(self, message: types.PingRequest) -> None:
        """Handle ping requests."""

    async def on_list_roots(self, message: types.ListRootsRequest) -> None:
        """Handle list roots requests."""

    async def on_create_message(self, message: types.CreateMessageRequest) -> None:
        """Handle create message requests."""

    async def on_elicit_complete(self, message: types.ElicitCompleteNotification) -> None:
        """Handle elicitation completion notifications.

        Sent by servers when a URL mode elicitation completes out-of-band.
        """
        logger.info("MCP elicitation completed", elicitation_id=message.params.elicitationId)
