# /// script
# dependencies = ["agentpool"]
# ///


"""Example comparing sequential and parallel downloads with agents.

This example demonstrates:
- Continuous repetitive tasks
- Async parallel execution of LLM calls
- YAML config definitions
- Capability use: list other agents and delegate tasks
- Simple stateful callback mechanism using a class
- Storage providers: SQLite and pretty-printed text files
"""

from dataclasses import dataclass
import os
from typing import TYPE_CHECKING, Any

from agentpool import AgentPool, AgentsManifest, Team, TeamRun
from agentpool.agents.events import RichAgentStreamEvent
from agentpool.docs.utils import get_config_path, is_pyodide, run


if TYPE_CHECKING:
    from agentpool.agents.context import AgentContext


# set your OpenAI API key here
os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "your_api_key_here")

FILE_URL = "http://speedtest.tele2.net/10MB.zip"
TEAM_PROMPT = f"Download this file: {FILE_URL}"
OVERSEER_PROMPT = f"""
Please coordinate downloading this file twice: {FILE_URL}

Delegate to file_getter_1 and file_getter_2. Report the results.
"""


def cheer(slogan: str) -> None:
    """🥳🎉 Cheer! Use this tool to show your apprreciation."""
    print(slogan)


@dataclass
class CheerProgress:
    """Class for tracking the progress of downloads and providing feedback."""

    def __init__(self) -> None:
        self.situation = "The team is assembling, ready to start the downloads!"

    def create_prompt(self) -> str:
        """Create a prompt for the fan based on current situation."""
        return f"Current situation: {self.situation}\nBe an enthusiastic and encouraging fan!"

    def update(self, situation: str) -> None:
        """Update the current situation and print it."""
        self.situation = situation
        print(situation)


async def run_example() -> None:
    # Load config from YAML
    config_path = get_config_path(None if is_pyodide() else __file__)
    manifest = AgentsManifest.from_file(config_path)

    async def event_handler(ctx: AgentContext[Any], event: RichAgentStreamEvent[Any]) -> None:
        from agentpool.agents.events import ToolCallProgressEvent

        if isinstance(event, ToolCallProgressEvent):
            print(f"Progress: {event.progress}/{event.total} - {event.message}")

    async with AgentPool(
        manifest,
        event_handlers=[event_handler],
    ) as pool:
        # Get agents from the YAML config
        worker_1 = pool.get_agent("file_getter_1")
        worker_2 = pool.get_agent("file_getter_2")
        fan = pool.get_agent("fan")
        progress = CheerProgress()

        # Run fan in background with progress updates
        await fan.run_in_background(progress.create_prompt)

        # Sequential downloads
        progress.update("Sequential downloads starting - let's see how they do!")
        sequential_team = TeamRun([worker_1, worker_2])
        sequential = await sequential_team.execute(TEAM_PROMPT)
        progress.update(f"Downloads completed in {sequential.duration:.2f} secs!")

        # Parallel downloads
        parallel_team = Team([worker_1, worker_2])
        parallel = await parallel_team.execute(TEAM_PROMPT)
        progress.update(f"Parallel downloads completed in {parallel.duration:.2f} secs!")

        # Overseer coordination
        overseer = pool.get_agent("overseer")
        result = await overseer.run(OVERSEER_PROMPT)
        progress.update(f"\nOverseer's report: {result.data}")

        await fan.stop()  # End of joy.


if __name__ == "__main__":
    run(run_example())
