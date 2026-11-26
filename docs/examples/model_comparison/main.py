# /// script
# dependencies = ["agentpool"]
# ///


"""Example of comparing different models using parallel teams.

This example demonstrates:
- Dynamic agent creation using different models
- Team creation and management
- Parallel execution for model comparison
- Result analysis and comparison
"""

import os

from agentpool import AgentPool, AgentsManifest
from agentpool.docs.utils import get_config_path, is_pyodide, run


# set your OpenAI API key here
os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "your_api_key_here")


PROMPT = """Please perform the following steps:

1. Create two agents:
   - Name: "gpt5_agent" using model "openai:gpt-5"
   - Name: "gpt5_mini_agent" using model "openai:gpt-5-mini"

2. Create a parallel team with the name "comparison_team".
   It should contain both agents just created.

3. Delegate this task to the team:
   "Explain the concept of quantum entanglement in exactly three sentences."

4. Analyze the responses, paying attention to:
   - Differences in explanation style and depth
   - Response time differences
   - Costs
   - Quality of the three-sentence constraint adherence
"""


async def run_example() -> None:
    """Run the model comparison example."""
    # Load config from YAML
    config_path = get_config_path(None if is_pyodide() else __file__)
    manifest = AgentsManifest.from_file(config_path)

    async with AgentPool(manifest) as pool:
        # Get the overseer agent with all needed capabilities
        overseer = pool.get_agent("overseer")

        print("\n=== Running Model Comparison ===")
        result = await overseer.run(PROMPT)
        print(result.content)


if __name__ == "__main__":
    run(run_example())
