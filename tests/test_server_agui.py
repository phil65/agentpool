"""Simple pydantic-ai AG-UI server for testing AGUIAgent."""

from __future__ import annotations

import argparse
import asyncio
from typing import TYPE_CHECKING

from pydantic_ai import Agent
from starlette.applications import Starlette
from starlette.routing import Route


if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response


# Create a simple pydantic-ai agent with TestModel
from pydantic_ai.models.test import TestModel


def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"The weather in {city} is sunny and 22°C."


def create_agent(with_tools: bool = False) -> Agent[None, str]:
    """Create the test agent, optionally with tools."""
    if with_tools:
        test_model = TestModel(call_tools=["get_weather"])
    else:
        test_model = TestModel(custom_output_text="The answer is 4.")

    agent = Agent(
        test_model,
        system_prompt="You are a helpful test assistant. Keep responses brief and clear.",
    )

    if with_tools:
        agent.tool_plain(get_weather)

    return agent


async def main(port: int = 8765, with_tools: bool = False) -> None:
    """Run the AG-UI test server."""
    import uvicorn

    agent = create_agent(with_tools=with_tools)

    async def handle_agent_request(request: Request) -> Response:
        """Handle AG-UI protocol requests."""
        from pydantic_ai.ui.ag_ui import AGUIAdapter

        return await AGUIAdapter.dispatch_request(request, agent=agent)

    app = Starlette(
        routes=[
            Route("/agent/run", handle_agent_request, methods=["POST"]),
        ],
    )

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--with-tools", action="store_true", help="Enable test tools (add, multiply, get_weather)"
    )
    args = parser.parse_args()
    asyncio.run(main(args.port, args.with_tools))
