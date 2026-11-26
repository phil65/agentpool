# /// script
# dependencies = ["agentpool"]
# ///

"""Demo: Agent using an MCP server that asks the user for input via elicitation."""

from pathlib import Path

import anyio

from agentpool import Agent
from agentpool_config.mcp_server import StdioMCPServerConfig


async def main() -> None:
    """Run the release workflow against the elicitation demo server."""
    server_path = Path(__file__).parent / "server.py"
    mcp_server = StdioMCPServerConfig(
        name="release_demo",
        command="uv",
        args=["run", str(server_path)],
    )
    agent = Agent(
        name="demo_agent",
        model="openai:gpt-6-luna",
        system_prompt="You are a helpful assistant with release tools.",
        mcp_servers=[mcp_server],
    )
    async with agent:
        print("Demo: Release workflow (Elicitation)")
        result = await agent.run("Use the release tool for package 'agentpool' at version 2.10.0.")
        print(f"\nAgent response:\n{result.data}")


if __name__ == "__main__":
    anyio.run(main)
