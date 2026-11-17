"""Runtime context models for Agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from agentpool.log import get_logger
from agentpool.messaging.context import NodeContext


if TYPE_CHECKING:
    from mcp.types import ElicitRequestParams, ElicitResult, ErrorData
    from upathtools.filesystems import IsolatedMemoryFileSystem, OverlayFileSystem

    from agentpool import Agent
    from agentpool.agents.events import StreamEventEmitter
    from agentpool.tools.base import Tool


ConfirmationResult = Literal["allow", "skip", "abort_run", "abort_chain"]

logger = get_logger(__name__)


@dataclass(kw_only=True)
class AgentContext[TDeps = Any](NodeContext[TDeps]):
    """Runtime context for agent execution.

    Generically typed with AgentContext[Type of Dependencies]
    """

    tool_name: str | None = None
    """Name of the currently executing tool."""

    tool_call_id: str | None = None
    """ID of the current tool call."""

    tool_input: dict[str, Any] = field(default_factory=dict)
    """Input arguments for the current tool call."""

    model_name: str | None = None
    """Model name in provider:model format (e.g., 'anthropic:claude-haiku-4-5')."""

    @property
    def native_agent(self) -> Agent[TDeps, Any]:
        """Current agent, type-narrowed to native pydantic-ai Agent."""
        from agentpool import Agent

        assert isinstance(self.node, Agent)
        return self.node

    async def handle_elicitation(self, params: ElicitRequestParams) -> ElicitResult | ErrorData:
        """Handle elicitation request for additional information."""
        provider = self.get_input_provider()
        return await provider.get_elicitation(params, tool_call_id=self.tool_call_id)

    async def report_progress(self, progress: float, total: float | None, message: str) -> None:
        """Report progress by emitting event into the agent's stream."""
        from agentpool.agents.events import ToolCallProgressEvent

        logger.info("Reporting tool call progress", progress=progress, total=total, message=message)
        progress_event = ToolCallProgressEvent(
            progress=int(progress),
            total=int(total) if total is not None else 100,
            message=message,
            tool_name=self.tool_name or "",
            tool_call_id=self.tool_call_id or "",
            tool_input=self.tool_input,
        )
        await self.agent._event_queue.put(progress_event)

    @property
    def events(self) -> StreamEventEmitter:
        """Get event emitter with context automatically injected."""
        from agentpool.agents.events import StreamEventEmitter

        return StreamEventEmitter(self)

    async def handle_confirmation(self, tool: Tool, args: dict[str, Any]) -> ConfirmationResult:
        """Handle tool execution confirmation.

        Returns "allow" if:
        - No confirmation handler is set
        - Handler confirms the execution

        Args:
            tool: The tool being executed
            args: Arguments passed to the tool

        Returns:
            Confirmation result indicating how to proceed
        """
        provider = self.get_input_provider()
        # Get tool_confirmation_mode if available (NativeAgent only)
        # Other agents handle permission checks in their own way
        mode = getattr(self.agent, "tool_confirmation_mode", "per_tool")
        if (mode == "per_tool" and not tool.requires_confirmation) or mode == "never":
            return "allow"
        return await provider.get_tool_confirmation(self, tool.description or "")

    @property
    def internal_fs(self) -> IsolatedMemoryFileSystem:
        """Access agent's internal filesystem for tool state.

        Tools can use this to store logs, history, temporary files, etc.
        The filesystem is scoped to the agent instance.

        Returns:
            In-memory filesystem for this agent
        """
        return self.agent.internal_fs

    @property
    def overlay_fs(self) -> OverlayFileSystem:
        """Access unified filesystem combining agent storage and VFS resources.

        Provides a layered view where writes go to agent's internal filesystem
        and reads fall through to VFS resources.

        Returns:
            OverlayFileSystem for this agent
        """
        return self.agent.overlay_fs
