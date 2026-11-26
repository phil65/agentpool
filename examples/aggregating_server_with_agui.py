"""Example demonstrating AggregatingServer with multiple protocol servers.

This example shows how to:
1. Create an AgentPool with multiple agents
2. Set up multiple HTTP servers (AG-UI, A2A) sharing the same pool
3. Run them together with coordinated lifecycle management
"""

import asyncio

import anyio

from agentpool import Agent, AgentPool
from agentpool_server import A2AServer, AggregatingServer, AGUIServer


async def main() -> None:
    """Run AggregatingServer with multiple protocol servers."""

    # Create multiple agents with different specializations
    def research_callback(message: str) -> str:
        return f"Research: Analyzing your query about: {message}"

    def analysis_callback(message: str) -> str:
        return f"Analysis: Here's my detailed analysis of: {message}"

    def summary_callback(message: str) -> str:
        return f"Summary: In brief, regarding {message}..."

    # Create agents
    research_agent = Agent.from_callback(name="researcher", callback=research_callback)
    analysis_agent = Agent.from_callback(name="analyzer", callback=analysis_callback)
    summary_agent = Agent.from_callback(name="summarizer", callback=summary_callback)

    # Create agent pool and register agents
    pool = AgentPool()
    pool.register("researcher", research_agent)
    pool.register("analyzer", analysis_agent)
    pool.register("summarizer", summary_agent)

    # Create protocol servers - each on its own port, sharing the same pool
    agui_server = AGUIServer(pool, host="localhost", port=8002, name="agui-server")
    a2a_server = A2AServer(pool, host="localhost", port=8001, name="a2a-server")

    # Create aggregating server to manage both servers together
    aggregating_server = AggregatingServer(
        pool,
        servers=[agui_server, a2a_server],
        name="multi-protocol-server",
    )

    print("Starting Aggregating Server...")
    print(f"\nAG-UI Server: {agui_server.base_url}")
    print("  AG-UI endpoints:")
    print("    - GET  /           (list agents)")
    print("    - POST /researcher")
    print("    - POST /analyzer")
    print("    - POST /summarizer")
    print(f"\nA2A Server: {a2a_server.base_url}")
    print("  A2A endpoints:")
    print("    - GET  /           (list agents)")
    print("    - POST /researcher")
    print("    - POST /analyzer")
    print("    - POST /summarizer")
    print("    - GET  /{agent}/.well-known/agent-card.json")

    print("\nPress Ctrl+C to stop all servers\n")

    async with aggregating_server, aggregating_server.run_context():
        print(f"\nServers running: {aggregating_server!r}")

        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            print("\nShutting down all servers...")


if __name__ == "__main__":
    """
    Example usage with curl:

    # List agents via AG-UI
    curl http://localhost:8002/

    # List agents via A2A
    curl http://localhost:8001/

    # Send request to researcher via AG-UI (SSE stream)
    curl -X POST http://localhost:8002/researcher \
      -H "Content-Type: application/json" \
      -H "Accept: text/event-stream" \
      -d '{
        "threadId": "thread-1",
        "runId": "run-1",
        "state": {},
        "messages": [
          {"id": "msg-1", "role": "user", "content": "Research quantum computing"}
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {}
      }'

    # Get agent card via A2A
    curl http://localhost:8001/analyzer/.well-known/agent-card.json

    Benefits of AggregatingServer:
    - Single pool shared across all protocol servers
    - Coordinated lifecycle management (start/stop together)
    - Easy to expose same agents via multiple protocols
    - Simpler error handling and cleanup
    """
    anyio.run(main)
