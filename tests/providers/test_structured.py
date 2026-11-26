from pydantic import BaseModel
import pytest

from agentpool import AgentPool, AgentsManifest


class Result(BaseModel):
    """Structured response result."""

    is_positive: bool


AGENT_CONFIG = """
agents:
    summarizer:
        model: {default_model}
        system_prompt: Summarize text in a structured way.
"""


@pytest.mark.integration
async def test_structured_response(default_model: str):
    manifest = AgentsManifest.from_yaml(AGENT_CONFIG.format(default_model=default_model))
    async with AgentPool(manifest) as pool:
        agent = pool.get_agent("summarizer", output_type=Result)
        result = await agent.run("I love this new feature!")
        assert result.data.is_positive
