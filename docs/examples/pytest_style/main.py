# /// script
# dependencies = ["agentpool"]
# ///


"""Example showing agent function discovery and execution.

This example demonstrates:
- Using agents as function decorators
- Automatic function discovery
- Dependency injection
- Execution order control
- Function result handling
"""

import os
from typing import TYPE_CHECKING

from agentpool.docs.utils import get_config_path, is_pyodide, run
from agentpool.running import node_function, run_nodes_async


if TYPE_CHECKING:
    from agentpool import Agent

# set your OpenAI API key here
os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "your_api_key_here")


DATA = """
Monthly Sales Data (2023):
Jan: $12,500
Feb: $15,300
Mar: $18,900
Apr: $14,200
May: $16,800
Jun: $21,500
"""


@node_function
async def analyze_data(analyzer: Agent) -> str:
    """First step: Analyze the data."""
    result = await analyzer.run(f"Analyze this sales data and identify trends:\n{DATA}")
    return result.data


@node_function(depends_on="analyze_data")
async def summarize_analysis(writer: Agent, analyze_data: str) -> str:
    """Second step: Create an executive summary."""
    prompt = f"Create a brief executive summary of this sales analysis:\n{analyze_data}"
    result = await writer.run(prompt)
    return result.data


async def run_example() -> None:
    """Run the analysis pipeline."""
    # Load config and run nodes
    config_path = get_config_path(None if is_pyodide() else __file__)
    results = await run_nodes_async(config_path, parallel=True)

    # Print results
    print("Analysis:", results["analyze_data"])
    print("Summary:", results["summarize_analysis"])


if __name__ == "__main__":
    run(run_example())
