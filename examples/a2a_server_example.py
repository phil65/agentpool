"""Example demonstrating A2AServer with multiple agents.

This example shows how to:
1. Create an AgentPool with multiple agents
2. Set up an A2AServer that exposes all agents
3. Access each agent via its own route with A2A protocol
"""

import asyncio

import anyio

from agentpool import Agent, AgentPool
from agentpool_server.a2a_server import A2AServer


async def main() -> None:
    """Run A2AServer example with multiple agents."""

    # Create multiple agents with different capabilities
    def math_agent_callback(message: str) -> str:
        """Math specialist agent."""
        return f"Math Agent: Let me solve that math problem: {message}"

    def code_agent_callback(message: str) -> str:
        """Code specialist agent."""
        return f"Code Agent: Here's how to implement that: {message}"

    def writing_agent_callback(message: str) -> str:
        """Writing specialist agent."""
        return f"Writing Agent: Let me help you write: {message}"

    math_agent = Agent.from_callback(name="math_specialist", callback=math_agent_callback)
    code_agent = Agent.from_callback(name="code_specialist", callback=code_agent_callback)
    writing_agent = Agent.from_callback(name="writing_specialist", callback=writing_agent_callback)
    pool = AgentPool()
    pool.register("math", math_agent)
    pool.register("code", code_agent)
    pool.register("writing", writing_agent)

    # Create A2AServer
    server = A2AServer(pool, host="localhost", port=8001, name="multi-agent-a2a-server")
    print("Starting A2A Server with multiple agents...")
    print(f"Server URL: {server.base_url}")
    print("\nAvailable agent endpoints:")
    for agent_name, urls in server.list_agent_routes().items():
        print(f"  - {agent_name}:")
        print(f"    Endpoint: POST {urls['endpoint']}")
        print(f"    Agent Card: GET {urls['agent_card']}")
        print(f"    Docs: GET {urls['docs']}")
    print("\nAgent list endpoint:")
    print(f"  - GET {server.base_url}/")
    print("\nPress Ctrl+C to stop the server\n")
    # Run server
    async with server, server.run_context():
        # Server is now running and handling requests
        # Each agent is accessible at:
        #   POST http://localhost:8001/math
        #   POST http://localhost:8001/code
        #   POST http://localhost:8001/writing
        # Agent cards:
        #   GET http://localhost:8001/math/.well-known/agent-card.json
        #   GET http://localhost:8001/code/.well-known/agent-card.json
        #   GET http://localhost:8001/writing/.well-known/agent-card.json
        # List all agents:
        #   GET http://localhost:8001/

        try:
            # Keep server running
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            print("\nShutting down server...")


if __name__ == "__main__":
    """
    Example usage with curl:

    # List all available agents
    curl http://localhost:8001/

    # Get agent card for math agent
    curl http://localhost:8001/math/.well-known/agent-card.json

    # Send A2A message to math agent
    curl -X POST http://localhost:8001/math \
      -H "Content-Type: application/json" \
      -d '{
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message/send",
        "params": {
          "messages": [
            {
              "role": "user",
              "parts": [
                {"kind": "text", "text": "What is 2+2?"}
              ]
            }
          ]
        }
      }'

    # Send A2A message to code agent
    curl -X POST http://localhost:8001/code \
      -H "Content-Type: application/json" \
      -d '{
        "jsonrpc": "2.0",
        "id": "2",
        "method": "message/send",
        "params": {
          "messages": [
            {
              "role": "user",
              "parts": [
                {"kind": "text", "text": "Write a hello world in Python"}
              ]
            }
          ]
        }
      }'

    # Send A2A message to writing agent
    curl -X POST http://localhost:8001/writing \
      -H "Content-Type: application/json" \
      -d '{
        "jsonrpc": "2.0",
        "id": "3",
        "method": "message/send",
        "params": {
          "messages": [
            {
              "role": "user",
              "parts": [
                {"kind": "text", "text": "Write a haiku about code"}
              ]
            }
          ]
        }
      }'

    # After receiving a task_id from message/send, check task status
    curl -X POST http://localhost:8001/math \
      -H "Content-Type: application/json" \
      -d '{
        "jsonrpc": "2.0",
        "id": "4",
        "method": "tasks/get",
        "params": {
          "task_id": "your-task-id-here"
        }
      }'
    """
    anyio.run(main)
