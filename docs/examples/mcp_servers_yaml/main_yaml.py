# /// script
# dependencies = ["agentpool"]
# ///


"""Example demonstrating MCP server integration with git tools.

This example shows:
- Using MCP servers to provide git functionality to agents
- Agent connections through YAML configuration
- Message flow between connected agents
- Team-level MCP server configuration
"""

import os

from agentpool import AgentPool, AgentsManifest
from agentpool.docs.utils import get_config_path, is_pyodide, run


PROMPT = "Get the latest commit hash!"

# set your OpenAI API key here
os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "your_api_key_here")


async def run_example() -> None:
    """Run example using YAML configuration."""
    # Load config from YAML
    config_path = get_config_path(None if is_pyodide() else __file__)
    manifest = AgentsManifest.from_file(config_path)

    async with AgentPool(manifest) as pool:
        # Get agents (connections already set up from YAML)
        picker = pool.get_agent("picker")
        analyzer = pool.get_agent("analyzer")

        # Register handlers to see messages
        picker.message_sent.connect(lambda msg: print(msg.format()))
        analyzer.message_sent.connect(lambda msg: print(msg.format()))

        # Start the chain
        await picker.run(PROMPT)


if __name__ == "__main__":
    run(run_example())
