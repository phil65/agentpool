"""Example demonstrating AGUIServer with multiple agents.

This example shows how to:
1. Create an AgentPool with multiple agents
2. Set up an AGUIServer that exposes all agents
3. Access each agent via its own route
"""

import anyio

from agentpool import Agent, AgentPool
from agentpool_server.agui_server import AGUIServer


async def main() -> None:
    """Run AGUIServer example with multiple agents."""

    # Create multiple agents with different capabilities
    def callback(message: str) -> str:
        """Example agent."""
        return f"Agent: Let me solve that math problem: {message}"

    math_agent = Agent.from_callback(name="math_specialist", callback=callback)
    pool = AgentPool()
    pool.register("math", math_agent)
    server = AGUIServer(pool, host="localhost", port=8002, name="multi-agent-agui-server")
    print("Starting AG-UI Server with multiple agents...")
    print(f"Server URL: {server.base_url}")
    print("\nAvailable agent endpoints:")
    for agent_name, url in server.list_agent_routes().items():
        print(f"  - {agent_name}: POST {url}")
    print("\nAgent list endpoint:")
    print(f"  - GET {server.base_url}/")
    print("\nPress Ctrl+C to stop the server\n")
    # Run server
    async with server, server.run_context():
        # Server is now running and handling requests
        # Each agent is accessible at:
        #   POST http://localhost:8002/math
        # List all agents:
        #   GET http://localhost:8002/

        try:
            # Keep server running
            await anyio.Event().wait()
        except KeyboardInterrupt:
            print("\nShutting down server...")


if __name__ == "__main__":
    """
    Example usage with curl:

    # List all available agents
    curl http://localhost:8002/

    # Send request to math agent
    curl -X POST http://localhost:8002/math \
      -H "Content-Type: application/json" \
      -d '{"messages": [{"role": "user", "content": "What is 2+2?"}]}'

    # Send request to code agent
    curl -X POST http://localhost:8002/code \
      -H "Content-Type: application/json" \
      -d '{"messages": [{"role": "user", "content": "Write a hello world"}]}'

    # Send request to writing agent
    curl -X POST http://localhost:8002/writing \
      -H "Content-Type: application/json" \
      -d '{"messages": [{"role": "user", "content": "Write a haiku"}]}'
    """
    anyio.run(main)
