# /// script
# dependencies = ["agentpool"]
# ///

"""Run round-robin example demonstrating cyclic communication pattern."""

import os

from agentpool_cli.run import run_command
from agentpool.docs.utils import get_config_path, is_pyodide


# set your OpenAI API key here
os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "your_api_key_here")


if __name__ == "__main__":
    # Use utils to get config path that works in both environments
    config_path = get_config_path(None if is_pyodide() else __file__)

    run_command(
        node_name="player1",
        prompts=["Start the word chain with: tree"],
        config_path=str(config_path),
        show_messages=True,
        detail_level="simple",
        show_metadata=False,
        show_costs=False,
        verbose=False,
    )
