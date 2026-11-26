# /// script
# dependencies = ["agentpool"]
# ///


"""Example of AI-Human interaction using agent capabilities.

This example demonstrates:
- Using a human agent for interactive input
- AI agent querying human agent when unsure
- Using the can_ask_agents capability
"""

import os

from agentpool import AgentPool, AgentsManifest
from agentpool.docs.utils import get_config_path, is_pyodide, run


# set your OpenAI API key here
os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "your_api_key_here")


QUESTION = """
What is the current status of Project DoomsDay?
This is crucial information that only a human would know.
If you don't know, ask the agent named "human".
"""


async def run_example() -> None:
    # Load config from YAML
    config_path = get_config_path(None if is_pyodide() else __file__)
    manifest = AgentsManifest.from_file(config_path)

    async with AgentPool(manifest) as pool:
        # Get the assistant agent
        assistant = pool.get_agent("assistant")

        # Run interaction
        await assistant.run(QUESTION)

        # Print conversation history
        print(await assistant.conversation.format_history())


if __name__ == "__main__":
    run(run_example())
