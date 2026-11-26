"""Runtime context models for Agents."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool.messaging.context import NodeContext


if TYPE_CHECKING:
    from mcp.types import ElicitRequestParams, ElicitResult, ErrorData
    from upathtools.filesystems import IsolatedMemoryFileSystem, OverlayFileSystem

    from agentpool import Agent
    from agentpool.agents.events import RichAgentStreamEvent, StreamEventEmitter
    from agentpool.agents.runtime import SessionRuntime
    from agentpool.tools.decisions import ToolDecision


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

    runtime: SessionRuntime[Any, Any] | None = field(default=None, repr=False)
    """The session runtime this context was created in, so events reach its turn's stream
    even when emitted from another task (e.g. an MCP client's progress callback)."""

    @property
    def native_agent(self) -> Agent[TDeps, Any]:
        """Current agent, type-narrowed to native pydantic-ai Agent."""
        from agentpool import Agent

        assert isinstance(self.node, Agent)
        return self.node

    @property
    def turn_session_id(self) -> str | None:
        """The session of the turn this context belongs to (None: find the running one)."""
        return self.runtime.session.session_id if self.runtime is not None else None

    async def handle_elicitation(self, params: ElicitRequestParams) -> ElicitResult | ErrorData:
        """Handle elicitation request for additional information."""
        provider = self.get_input_provider()
        with self.agent.awaiting_user(self.turn_session_id):
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
        await self.emit_event(progress_event)

    async def emit_event(self, event: RichAgentStreamEvent[Any]) -> None:
        """Emit an event into the stream of the turn (session runtime) this context belongs to."""
        if self.runtime is None:
            await self.agent.emit_event(event)
            return
        from agentpool.agents.runtime import activate

        with activate(self.agent, self.runtime):
            await self.agent.emit_event(event)

    @property
    def events(self) -> StreamEventEmitter:
        """Get event emitter with context automatically injected."""
        from agentpool.agents.events import StreamEventEmitter

        return StreamEventEmitter(self)

    def stop_turn(self) -> None:
        """End this context's turn, after the callback deciding a tool call has answered.

        For a denial that stops the turn (``Deny(stop=True)``).
        """
        self.agent.stop_turn_soon(self.turn_session_id)

    async def decide_tool(self, tool_description: str = "") -> ToolDecision:
        """The user's decision on this context's tool call (``tool_name``, ``tool_input``).

        A decision the user made for the whole session answers without asking. Otherwise
        the input provider asks, while the turn's session ``requires_action``, and a
        decision for the session (``scope="session"``) is kept for the tool's later calls.

        Args:
            tool_description: Human-readable description of the tool
        """
        from agentpool.tools.decisions import from_remembered

        session = self.runtime.session if self.runtime else self.agent.turn_session()
        name = self.tool_name or "unknown"
        if session is not None and (kept := session.overrides.tool_decisions.get(name)):
            logger.debug("Tool decided for the session", tool_name=name, decision=kept)
            return from_remembered(kept)
        provider = self.get_input_provider()
        with self.agent.awaiting_user(self.turn_session_id):
            decision = await provider.get_tool_confirmation(self, tool_description)
        if decision.scope == "session" and session is not None:
            session.overrides.tool_decisions[name] = decision.remembered()
        return decision

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
