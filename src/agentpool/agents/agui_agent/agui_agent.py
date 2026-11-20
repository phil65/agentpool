"""AG-UI remote agent implementation.

This module provides a MessageNode adapter that connects to remote AG-UI protocol servers,
enabling remote agent execution with streaming support.

Supports client-side tool execution: tools can be defined locally and sent to the
remote AG-UI agent. When the agent requests tool execution, the tools are executed
locally and results sent back.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast
from uuid import uuid4

from anyenv.processes import hard_kill
import anyio
import httpx
from pydantic_ai import ModelRequest, ToolReturnPart

from agentpool.agents.agui_agent.helpers import execute_tool_call, parse_sse_stream
from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import (
    PartStartEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
)
from agentpool.agents.events.reconstructor import MessageReconstructor
from agentpool.agents.exceptions import (
    AgentNotInitializedError,
    OperationNotAllowedError,
    UnknownModeError,
)
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.tools import ToolManager
from agentpool.utils.subprocess_utils import start_process
from agentpool.utils.token_breakdown import calculate_usage_from_parts


if TYPE_CHECKING:
    from asyncio.subprocess import Process
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType

    from ag_ui.core import Message, ToolMessage
    from evented_config import EventConfig
    from exxec import ExecutionEnvironment
    from pydantic_ai import UserContent
    from slashed import BaseCommand
    from tokonomics.model_discovery.model_info import ModelInfo

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ModeCategory
    from agentpool.common_types import AnyEventHandlerType, StrPath, ToolType
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import MessageHistory
    from agentpool.models.agui_agents import AGUIAgentConfig
    from agentpool.sessions import SessionData
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig
    from agentpool_config.nodes import ToolConfirmationMode


logger = get_logger(__name__)


def get_client(headers: dict[str, str], timeout: float) -> httpx.AsyncClient:
    headers = {**headers, "Accept": "text/event-stream", "Content-Type": "application/json"}
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout), headers=headers)


class AGUIAgent[TDeps = None](BaseAgent[TDeps, str]):
    """Agent backed by a remote AG-UI protocol server.

    Connects to AG-UI compatible endpoints via HTTP/SSE and provides the same
    interface as native agents, enabling composition with other nodes via
    connections, teams, etc.

    The agent manages:
    - HTTP client lifecycle (create on enter, close on exit)
    - AG-UI protocol communication via SSE streams
    - Event conversion to native agentpool events
    - Message accumulation and final response generation
    - Client-side tool execution (tools defined locally, executed when requested)
    - Chunk transformation for compatibility with different server modes

    Client-Side Tools:
        Tools can be registered with this agent and sent to the remote AG-UI server.
        When the server requests a tool call, the tool is executed locally and the
        result is sent back. This enables human-in-the-loop workflows and local
        capability exposure to remote agents.
    """

    AGENT_TYPE: ClassVar = "agui"

    def __init__(
        self,
        endpoint: str,
        *,
        deps_type: type[TDeps] | None = None,
        name: str = "agui-agent",
        description: str | None = None,
        display_name: str | None = None,
        timeout: float = 60.0,
        headers: dict[str, str] | None = None,
        startup_command: str | None = None,
        startup_delay: float = 2.0,
        tools: Sequence[ToolType] | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        env: ExecutionEnvironment | StrPath | None = None,
        agent_pool: AgentPool[Any] | None = None,
        enable_logging: bool = True,
        event_configs: Sequence[EventConfig] | None = None,
        input_provider: InputProvider | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        tool_confirmation_mode: ToolConfirmationMode = "per_tool",
        commands: Sequence[BaseCommand] | None = None,
        hooks: AgentHooks | None = None,
    ) -> None:
        """Initialize AG-UI agent client.

        Args:
            endpoint: HTTP endpoint for the AG-UI agent
            deps_type: Type of dependencies to use
            name: Agent name for identification
            description: Agent description
            display_name: Human-readable display name
            timeout: Request timeout in seconds
            headers: Additional HTTP headers
            startup_command: Optional shell command to start server automatically.
                           Useful for testing - server lifecycle is managed by the agent.
                           Example: "agentpool serve-agui config.yml"
            startup_delay: Seconds to wait after starting server before connecting (default: 2.0)
            tools: Tools to expose to the remote agent (executed locally when called)
            mcp_servers: MCP servers to connect
            env: Execution environment
            agent_pool: Agent pool for multi-agent coordination
            enable_logging: Whether to enable database logging
            input_provider: Provider for user input/confirmations
            event_configs: Event trigger configurations
            event_handlers: Sequence of event handlers to register
            tool_confirmation_mode: Tool confirmation mode for local tool execution
            commands: Slash commands
            hooks: Agent hooks for pre/post tool execution
        """
        super().__init__(
            name=name,
            description=description,
            display_name=display_name,
            mcp_servers=mcp_servers,
            deps_type=deps_type,
            env=env,
            agent_pool=agent_pool,
            enable_logging=enable_logging,
            event_configs=event_configs,
            event_handlers=event_handlers,
            input_provider=input_provider,
            commands=commands,
            hooks=hooks,
        )
        # Tool confirmation mode for local tool execution
        self.tool_confirmation_mode: ToolConfirmationMode = tool_confirmation_mode
        # AG-UI specific configuration
        self.endpoint = endpoint
        self.timeout = timeout
        self.headers = headers or {}
        # Startup command configuration
        self._startup_command = startup_command
        self._startup_delay = startup_delay
        self._startup_process: Process | None = None
        # Client state
        self._client: httpx.AsyncClient | None = None
        self._sdk_session_id: str | None = None
        # Override tools with provided tools
        self.tools = ToolManager(tools)

    @classmethod
    def from_config(
        cls,
        config: AGUIAgentConfig,
        *,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        agent_pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,
    ) -> Self:
        """Create an AGUIAgent from a config object."""
        # Merge config-level handlers with provided handlers
        config_handlers = config.get_event_handlers()
        merged_handlers: list[AnyEventHandlerType] = [*config_handlers, *(event_handlers or [])]
        return cls(
            endpoint=config.endpoint,
            name=config.name or "agui-agent",
            deps_type=deps_type,
            description=config.description,
            display_name=config.display_name,
            event_handlers=merged_handlers or None,
            env=config.get_execution_environment(),
            timeout=config.timeout,
            input_provider=input_provider,
            headers=config.get_headers(),
            startup_command=config.startup_command,
            startup_delay=config.startup_delay,
            tools=[tool_config.get_tool() for tool_config in config.tools],
            mcp_servers=config.mcp_servers,
            agent_pool=agent_pool,
            tool_confirmation_mode=config.requires_tool_confirmation,
            hooks=config.hooks.get_agent_hooks(),
        )

    async def __aenter__(self) -> Self:
        """Enter async context - initialize client and base resources."""
        await super().__aenter__()
        self._client = get_client(self.headers, self.timeout)
        self._sdk_session_id = self.session_id
        if self._startup_command:  # Start server if startup command is provided
            self._startup_process = await start_process(self._startup_command, self._startup_delay)
        self.log.debug("AG-UI client initialized", endpoint=self.endpoint)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context - cleanup client and base resources."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._sdk_session_id = None
        if self._startup_process:  # Stop server if we started it
            if not self._startup_process:
                return

            self.log.info("Stopping AG-UI server")
            try:
                await hard_kill(self._startup_process)  # Use cross-platform hard kill helper
            except Exception:  # Log but don't fail if kill has issues
                self.log.exception("Error during process termination")
            finally:
                self._startup_process = None
                self.log.info("AG-UI server stopped")
        self.log.debug("AG-UI client closed")
        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def set_tool_confirmation_mode(self, mode: str) -> None:
        """Set the tool confirmation mode for local tool execution.

        Args:
            mode: Tool confirmation mode (always, never, per_tool)

        Raises:
            ValueError: If mode is not a valid ToolConfirmationMode
        """
        from agentpool_config.nodes import ToolConfirmationMode

        valid_modes: set[str] = {"always", "never", "per_tool"}
        if mode not in valid_modes:
            raise UnknownModeError(mode, list(valid_modes))
        self.tool_confirmation_mode = cast(ToolConfirmationMode, mode)
        self.log.info("Tool confirmation mode changed", mode=mode)

    async def _interrupt(self) -> None:
        """Cancel the current stream task."""
        if self._current_stream_task and not self._current_stream_task.done():
            self._current_stream_task.cancel()

    async def _stream_events(  # noqa: PLR0915
        self,
        prompts: list[UserContent],
        *,
        user_msg: ChatMessage[Any],
        message_history: MessageHistory,
        effective_parent_id: str | None,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_id: str | None = None,
        input_provider: InputProvider | None = None,
        deps: TDeps | None = None,
        wait_for_connections: bool | None = None,
        store_history: bool = True,
    ) -> AsyncIterator[RichAgentStreamEvent[str]]:
        from ag_ui.core import RunAgentInput, UserMessage

        from agentpool.agents.agui_agent.agui_converters import (
            model_messages_to_agui,
            to_agui_input_content,
            to_agui_tool,
        )

        # Resolve input provider: explicit parameter overrides agent default
        effective_input_provider = input_provider or self._input_provider

        if not self._client:
            raise AgentNotInitializedError

        # Set thread_id from session_id (needed for AG-UI protocol)
        if self._sdk_session_id is None:
            self._sdk_session_id = self.session_id

        run_id = str(uuid4())  # New run ID for each run
        reconstructor = MessageReconstructor(initial_prompts=prompts)
        assert self.session_id is not None  # Initialized by BaseAgent.run_stream()
        thread_id = self._sdk_session_id or self.session_id
        yield RunStartedEvent(session_id=thread_id, run_id=run_id, agent_name=self.name)
        # Convert existing conversation history to AG-UI format
        # AG-UI protocol expects full history with each request (stateless server)
        # Extract ModelMessages from ChatMessages
        model_msgs = [m for chat_msg in message_history.get_history() for m in chat_msg.messages]
        history_messages = list(model_messages_to_agui(model_msgs))
        # Convert new user message content to AG-UI format
        final_content = to_agui_input_content(prompts)
        user_message = UserMessage(id=str(uuid4()), content=final_content)
        # Build messages list: history + new user message
        messages: list[Message] = [*history_messages, user_message]
        pending_tool_results: list[ToolMessage] = []
        tools = await self.tools.get_tools(state="enabled")
        try:  # Loop to handle tool calls - agent may request multiple rounds
            while True:
                # Check for cancellation at start of each iteration
                if self._cancelled:
                    self.log.info("Stream cancelled by user")
                    break

                request_data = RunAgentInput(
                    thread_id=self._sdk_session_id or self.session_id,
                    run_id=run_id,
                    state={},
                    messages=messages,
                    tools=[to_agui_tool(t) for t in tools],
                    context=[],
                    forwarded_props={},
                )

                data = request_data.model_dump(by_alias=True)
                tool_calls_pending: dict[str, tuple[str, dict[str, Any]]] = {}
                try:
                    async with self._client.stream("POST", self.endpoint, json=data) as response:
                        response.raise_for_status()
                        async for event in self._process_events(
                            response=response,
                            tool_calls_pending=tool_calls_pending,
                        ):
                            reconstructor.observe(event)
                            yield event
                except httpx.HTTPError:
                    self.log.exception("HTTP error during AG-UI run")
                    raise
                # If cancelled or no pending tool calls, break out of the while loop
                if self._cancelled or not tool_calls_pending:
                    break
                # Execute pending tool calls locally and collect results
                tools_by_name = {t.name: t for t in tools}
                base_ctx = self.get_context(data=deps, input_provider=effective_input_provider)
                pending_tool_results = []
                for tc_id, (tool_name, args) in tool_calls_pending.items():
                    tool = tools_by_name.get(tool_name)
                    if tool is None:
                        logger.debug("Skipping server-side tool", tool=tool_name)
                        continue
                    ctx = replace(
                        base_ctx,
                        tool_call_id=tc_id,
                        tool_name=tool_name,
                        tool_input=args,
                    )
                    result = await execute_tool_call(
                        tool,
                        ctx,
                        confirmation_mode=self.tool_confirmation_mode,
                    )
                    pending_tool_results.append(result)
                    yield ToolCallCompleteEvent(
                        tool_name=tool_name,
                        tool_call_id=tc_id,
                        tool_input=args,
                        tool_result=result.content,
                        agent_name=self.name,
                        message_id=message_id or "",
                        error=result.error,
                    )
                # If no results (all tools were server-side), we're done
                if not pending_tool_results:
                    break
                # Flush current response parts and add tool returns
                reconstructor.flush()
                for r in pending_tool_results:
                    tool_name = tool_calls_pending.get(r.tool_call_id, ("unknown", {}))[0]
                    return_part = ToolReturnPart(
                        tool_name=tool_name,
                        content=r.content,
                        tool_call_id=r.tool_call_id,
                    )
                    reconstructor.model_messages.append(ModelRequest(parts=[return_part]))
                # Add tool results to messages for next iteration
                messages = [*pending_tool_results]
                self.log.debug("Continuing with tool results", count=len(pending_tool_results))
        except asyncio.CancelledError:
            self.log.info("Stream cancelled via task cancellation")
            self._cancelled = True

        reconstructor.flush()

        if self._cancelled:
            final_message = ChatMessage[str](
                content=reconstructor.text_content,
                role="assistant",
                name=self.name,
                message_id=message_id or str(uuid4()),
                session_id=self.session_id,
                parent_id=user_msg.message_id,
                messages=reconstructor.model_messages,
                finish_reason="stop",
            )
            yield StreamCompleteEvent(message=final_message)
            return

        # Final drain of event queue after stream completes
        async for e in self._drain_event_queue():
            yield e
        # Calculate approximate token usage from what we can observe
        usage, cost_info = await calculate_usage_from_parts(
            input_parts=prompts,
            response_parts=reconstructor.all_response_parts,
            text_content=reconstructor.text_content,
            model_name=self.model_name,
        )

        final_message = ChatMessage[str](
            content=reconstructor.text_content,
            role="assistant",
            name=self.name,
            message_id=message_id or str(uuid4()),
            session_id=self.session_id,
            parent_id=user_msg.message_id,
            messages=reconstructor.model_messages,
            usage=usage,
            cost_info=cost_info,
        )
        yield StreamCompleteEvent(message=final_message)  # Post-processing handled by base class

    async def _drain_event_queue(self) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Drain the event queue and yield events."""
        while not self._event_queue.empty():
            try:
                yield self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _process_events(
        self,
        response: httpx.Response,
        tool_calls_pending: dict[str, tuple[str, dict[str, Any]]],
    ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        from ag_ui.core import (
            ToolCallArgsEvent as AGUIToolCallArgsEvent,
            ToolCallEndEvent as AGUIToolCallEndEvent,
            ToolCallResultEvent as AGUIToolCallResultEvent,
            ToolCallStartEvent as AGUIToolCallStartEvent,
        )

        from agentpool.agents.agui_agent.agui_converters import (
            agui_to_native_event,
            tool_result_text,
        )
        from agentpool.agents.agui_agent.chunk_transformer import ChunkTransformer
        from agentpool.agents.tool_call_accumulator import ToolCallAccumulator

        accumulator = ToolCallAccumulator()
        chunk_transformer = ChunkTransformer()  # Create chunk transformer for this run
        async for raw_event in parse_sse_stream(response):
            # Check for cancellation during streaming
            if self._cancelled:
                self.log.info("Stream cancelled during event processing")
                break
            # Transform chunks to proper START/CONTENT/END sequences
            for event in chunk_transformer.transform(raw_event):
                match event:  # Handle events for tool call accumulation
                    case AGUIToolCallStartEvent(tool_call_id=tc_id, tool_call_name=name) if name:
                        accumulator.start(tc_id, name)
                    case AGUIToolCallArgsEvent(tool_call_id=tc_id, delta=delta):
                        accumulator.add_args(tc_id, delta)
                    case AGUIToolCallEndEvent(tool_call_id=tc_id) if result := accumulator.complete(
                        tc_id
                    ):
                        tool_name, args = result
                        tool_calls_pending[tc_id] = (tool_name, args)
                        # Emit PartStartEvent so reconstructor can track the tool call
                        yield PartStartEvent.tool_call(
                            index=0,
                            tool_name=tool_name,
                            args=args,
                            tool_call_id=tc_id,
                        )
                    # Server-side tools: the remote agent reports the result.
                    case AGUIToolCallResultEvent(tool_call_id=tc_id, content=content):
                        tool_name, args = tool_calls_pending.pop(tc_id, ("unknown", {}))
                        yield ToolCallCompleteEvent(
                            tool_name=tool_name,
                            tool_call_id=tc_id,
                            tool_input=args,
                            tool_result=tool_result_text(content),
                            agent_name=self.name,
                            message_id=event.message_id,
                        )

                # Convert to native event and distribute to handlers
                for native_event in agui_to_native_event(event):
                    async for e in self._drain_event_queue():
                        yield e
                    yield native_event

        # Flush any pending chunk events at end of stream
        for event in chunk_transformer.flush():
            for native_event in agui_to_native_event(event):
                yield native_event

    @property
    def model_name(self) -> str | None:
        """Get model name (AG-UI doesn't expose this)."""
        return None

    async def set_model(self, model: str) -> None:
        """Set model (no-op for AG-UI as model is controlled by remote server)."""
        # AG-UI agents don't support model selection - the model is
        # determined by the remote server configuration

    async def get_available_models(self) -> list[ModelInfo] | None:
        """Get available models for AG-UI agent.

        AG-UI doesn't expose model information, so returns a placeholder model
        indicating the model is determined by the remote server.

        Returns:
            List with a single placeholder ModelInfo
        """
        from tokonomics.model_discovery.model_info import ModelInfo

        return [
            ModelInfo(
                id="server-determined",
                name="Determined by server",
                description="The model is determined by the remote AG-UI server",
            )
        ]

    async def get_modes(self) -> list[ModeCategory]:
        """Get available modes for AG-UI agent (not supported)."""
        return []

    async def _set_mode(self, mode_id: str | bool, category_id: str) -> None:
        """AG-UI doesn't support mode switching."""
        raise OperationNotAllowedError("mode switching (model is controlled by remote server)")

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """List sessions for AG-UI agent (not supported)."""
        return []

    async def start_new_session(self) -> None:
        """Start a fresh AG-UI thread (derived from the next session ID)."""
        await super().start_new_session()
        self._sdk_session_id = None

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load a session for AG-UI agent. (not supported)."""
        self.log.warning("AG-UI agents do not support session loading", session_id=session_id)
        return None


if __name__ == "__main__":

    async def main() -> None:
        """Example usage."""
        endpoint = "http://localhost:8000/agent/run"
        async with AGUIAgent(endpoint=endpoint, name="test-agent") as agent:
            result = await agent.run("What is 2+2?")
            print(f"Result: {result.content}")
            print("\nStreaming:")
            async for event in agent.run_stream("Tell me a short joke"):
                print(f"Event: {event}")

    anyio.run(main)
