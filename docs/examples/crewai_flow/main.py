# /// script
# dependencies = ["agentpool"]
# ///

"""Adaption of a CrewAI-like flow."""

import os

from agentpool import Agent, AgentsManifest
from agentpool.docs.utils import get_config_path, is_pyodide, run
from agentpool.running import node_function, run_nodes_async


# set your OpenAI API key here
os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "your_api_key_here")


@node_function
async def generate_city(city_picker: Agent[None]) -> str:
    """Generate a random city name."""
    result = await city_picker.run("Return the name of a random city in the world.")
    return result.data


@node_function(depends_on=generate_city)
async def generate_fun_fact(fact_finder: Agent[None], generate_city: str) -> str:
    """Generate fun fact about the city."""
    result = await fact_finder.run(f"Tell me a fun fact about {generate_city}")
    return result.data


async def run_example() -> None:
    """Run the CrewAI-like flow example."""
    config_path = get_config_path(None if is_pyodide() else __file__)
    manifest = AgentsManifest.from_file(config_path)
    results = await run_nodes_async(manifest)
    print(f"City: {results['generate_city']}")
    print(f"Fun fact: {results['generate_fun_fact']}")


if __name__ == "__main__":
    run(run_example())
