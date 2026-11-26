# /// script
# dependencies = ["agentpool", "mypy"]
# ///


"""Agentsoft Corp. 3 agents publishing software.

This example shows:
1. Async delegation: File scanner delegates to doc writer (fire and forget)
2. Tool usage (async + wait): File scanner uses error checker as a tool (wait for result)
3. Chained tool calls.
"""

import os
from pathlib import Path

from mypy import api
import rich

from agentpool import AgentPool, AgentsManifest, Agent
from agentpool.docs.utils import run


# set your OpenAI API key here
os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "your_api_key_here")


def check_types(path: str) -> str:
    """Type check Python file using mypy."""
    stdout, _stderr, _code = api.run([path])
    return stdout


async def main() -> None:
    # Load config from YAML
    config_path = Path(__file__).parent / "config.yml"
    manifest = AgentsManifest.from_file(config_path)

    async with AgentPool(manifest) as pool:
        scanner = pool.get_agent("file_scanner")
        writer = pool.get_agent("doc_writer")
        checker = pool.get_agent("error_checker")

        # Set up message logging
        for agent in (scanner, writer, checker):
            agent.message_sent.connect(lambda msg: rich.print(msg.format()))

        # Setup chain: scanner -> writer -> console output
        scanner.connect_to(writer)

        # Start async docs generation (the writer will start working in async fashion)
        await scanner.run('List all Python files in "src/agentpool/agent"')
        assert isinstance(scanner, Agent)
        # Use error checker as tool (this blocks until complete)
        scanner.register_worker(checker)
        prompt = 'Check types for all Python files in "src/agentpool/agent"'
        result = await scanner.run(prompt)
        rich.print(f"Type checking result:\n{result.data}")

        # Wait for documentation to finish
        await writer.task_manager.complete_tasks()


if __name__ == "__main__":
    run(main())
