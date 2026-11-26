"""AG-UI adapter for BaseAgent.

This adapter allows any BaseAgent (native, ACP, Claude Code, Codex, etc.)
to be exposed via the AG-UI protocol. It transforms our event stream
to AG-UI events using pydantic-ai's AGUIEventStream.

Since our RichAgentStreamEvent is a superset of pydantic-ai's AgentStreamEvent,
the core streaming events (text, thinking, tool calls) work automatically.
Custom events like ToolCallProgressEvent are silently ignored.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ag_ui.core import BaseEvent, RunAgentInput
    from starlette.requests import Request
    from starlette.responses import StreamingResponse

    from agentpool.agents.base_agent import BaseAgent


__all__ = ["BaseAgentAGUIAdapter"]


SSE_CONTENT_TYPE = "text/event-stream"


@dataclass
class BaseAgentAGUIAdapter:
    """AG-UI adapter for BaseAgent instances.

    This adapter bridges any BaseAgent to the AG-UI protocol by:
    1. Parsing the incoming AG-UI request
    2. Running the agent with run_stream()
    3. Transforming events through AGUIEventStream
    4. Returning a streaming response

    Example:
        adapter = BaseAgentAGUIAdapter(agent, run_input)
        response = adapter.streaming_response()
    """

    agent: BaseAgent[Any, Any]
    """The agent to run."""

    run_input: RunAgentInput
    """The parsed AG-UI request input."""

    accept: str | None = None
    """Accept header for response encoding."""

    @classmethod
    async def from_request(
        cls,
        request: Request,
        agent: BaseAgent[Any, Any],
    ) -> BaseAgentAGUIAdapter:
        """Create adapter from an incoming request.

        Args:
            request: The Starlette/FastAPI request
            agent: The agent to run

        Returns:
            Configured adapter instance
        """
        from ag_ui.core import RunAgentInput

        body = await request.body()
        run_input = RunAgentInput.model_validate_json(body)
        accept = request.headers.get("accept")
        return cls(agent=agent, run_input=run_input, accept=accept)

    def _get_user_prompt(self) -> str:
        """Extract user prompt from AG-UI messages."""
        # Get the last user message from the input
        from ag_ui.core import UserMessage

        for msg in reversed(self.run_input.messages):
            if isinstance(msg, UserMessage):
                if isinstance(msg.content, str):
                    return msg.content
                # Handle structured content
                from ag_ui.core import TextInputContent

                for part in msg.content:
                    if isinstance(part, TextInputContent):
                        return part.text
        return ""

    async def run_stream(self) -> AsyncIterator[BaseEvent]:
        """Run the agent and yield AG-UI events.

        Yields:
            AG-UI protocol events
        """
        from pydantic_ai.ui.ag_ui import AGUIEventStream

        # Create event stream transformer
        event_stream = AGUIEventStream(self.run_input, accept=self.accept)

        # Yield start events
        async for event in event_stream.before_stream():
            yield event

        try:
            # Get user prompt and run agent
            # TODO: Properly handle agent statefulness with AG-UI protocol.
            # AG-UI is stateless - client sends full history with each request.
            # For now, we use store_history=False to avoid accumulating duplicate
            # history. The proper solution would be to convert the client's AG-UI
            # message history to our format and use it as the conversation context.
            prompt = self._get_user_prompt()

            async for agent_event in self.agent.run_stream(prompt, store_history=False):
                # Transform compatible events through AGUIEventStream
                # Our RichAgentStreamEvent is a superset - AGUIEventStream handles
                # the pydantic-ai compatible events and ignores unknown types
                async for agui_event in event_stream.handle_event(agent_event):  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
                    yield agui_event

        except Exception as e:  # noqa: BLE001
            async for event in event_stream.on_error(e):
                yield event

        # Yield end events
        async for event in event_stream.after_stream():
            yield event

    def encode_stream(self, stream: AsyncIterator[BaseEvent]) -> AsyncIterator[str]:
        """Encode events as SSE strings."""
        from ag_ui.encoder import EventEncoder

        encoder = EventEncoder(accept=self.accept or SSE_CONTENT_TYPE)

        async def _encode() -> AsyncIterator[str]:
            async for event in stream:
                yield encoder.encode(event)

        return _encode()

    def streaming_response(self) -> StreamingResponse:
        """Create a streaming HTTP response.

        Returns:
            Starlette StreamingResponse with AG-UI events
        """
        from ag_ui.encoder import EventEncoder
        from starlette.responses import StreamingResponse

        encoder = EventEncoder(accept=self.accept or SSE_CONTENT_TYPE)
        media_type = encoder.get_content_type()
        return StreamingResponse(self.encode_stream(self.run_stream()), media_type=media_type)

    @classmethod
    async def dispatch_request(
        cls,
        request: Request,
        agent: BaseAgent[Any, Any],
    ) -> StreamingResponse:
        """Handle an AG-UI request end-to-end.

        This is the main entry point for handling AG-UI requests.

        Args:
            request: The incoming request
            agent: The agent to run

        Returns:
            Streaming response with AG-UI events
        """
        adapter = await cls.from_request(request, agent)
        return adapter.streaming_response()
