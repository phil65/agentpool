from typing import TYPE_CHECKING

from pydantic import BaseModel
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool, AgentsManifest


if TYPE_CHECKING:
    from pathlib import Path


class StructuredResponse(BaseModel):
    """Test model for structured output."""

    message: str
    value: int


BASIC_WORKERS = """\
agents:
  main:
    type: native
    model: test
    display_name: Main Agent
    workers:
      - worker
      - specialist
    system_prompt: "You are the main agent. Use your workers to help with tasks."

  worker:
    type: native
    model: test
    display_name: Basic Worker
    system_prompt: "You are a helpful worker agent."

  specialist:
    type: native
    model: test
    display_name: Domain Specialist
    system_prompt: "You are a specialist with deep domain knowledge."
"""

WORKERS_WITH_SHARING = """\
agents:
  main:
    type: native
    model: test
    display_name: Main Agent
    workers:
      - name: worker
        type: agent
        pass_message_history: true
      - specialist

  worker:
    type: native
    model: test
    display_name: History Worker
    system_prompt: "You are a worker with conversation history."

  specialist:
    type: native
    model: test
    display_name: Context Worker
    system_prompt: "You are a worker with context access."
"""

INVALID_WORKERS = """\
agents:
  main:
    type: native
    model: test
    display_name: Main Agent
    workers:
      - nonexistent
"""

STRUCTURED_WORKER = """\
agents:
  main:
    type: native
    model: test
    display_name: Main Agent
    workers:
      - structured_worker

  structured_worker:
    model: test
    display_name: Structured Worker
    system_prompt: "You are a worker that returns structured data."
"""


def write_config(content: str, path: Path) -> Path:
    """Write config content to a file."""
    config_file = path / "agents.yml"
    config_file.write_text(content)
    return config_file


async def test_basic_worker_setup(tmp_path: Path):
    """Test basic worker registration and usage."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = pool.get_agent("main")
        # Verify workers were registered as tools via toolset
        tools = await main_agent.tools.get_tools()
        tool_names = [t.name for t in tools]
        assert "ask_worker" in tool_names
        assert "ask_specialist" in tool_names


@pytest.mark.integration
@pytest.mark.live_model
async def test_history_sharing(tmp_path: Path):
    """Test history sharing between agents."""
    config_path = write_config(WORKERS_WITH_SHARING, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = pool.get_agent("main")
        worker = pool.get_agent("worker")
        # Configure models: real model for main agent, TestModel for worker
        await main_agent.set_model("openai:gpt-6-luna")
        worker_model = TestModel(custom_output_text="The value is 42")
        assert isinstance(worker, Agent)
        await worker.set_model(worker_model)
        # Create some conversation history
        result = await main_agent.run("Remember X equals 42")
        # Worker should have access to history
        result = await main_agent.run("Ask worker: What is X?")
        assert "42" in result.content


async def test_worker_context_sharing(tmp_path: Path):
    """Test context sharing between agents."""
    config_path = write_config(WORKERS_WITH_SHARING, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = pool.get_agent("main", deps_type=dict)
        specialist = pool.get_agent("specialist")
        assert isinstance(main_agent, Agent)
        assert isinstance(specialist, Agent)
        main_model = TestModel(call_tools=["ask_specialist"])
        specialist_model = TestModel(custom_output_text="I can see context value: 123")
        await main_agent.set_model(main_model)
        await specialist.set_model(specialist_model)
        prompt = "Ask specialist: What's in the context?"
        result = await main_agent.run(prompt, deps={"important_value": 123})
        assert "123" in result.data


async def test_invalid_worker(tmp_path: Path):
    """Test error when using non-existent worker."""
    config_path = write_config(INVALID_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    # With toolset approach, error happens at tool call time, not pool init
    async with AgentPool(manifest) as pool:
        main_agent = pool.get_agent("main")
        # Tool is created but will fail when called
        tools = await main_agent.tools.get_tools()
        tool_names = [t.name for t in tools]
        assert "ask_nonexistent" in tool_names


async def test_worker_independence(tmp_path: Path):
    """Test that workers maintain independent state when not sharing."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = pool.get_agent("main")
        # Create history in main agent
        await main_agent.run("Remember X equals 42")
        # Worker should not see this history
        result = await main_agent.run("Ask worker: What is X?")
        assert "42" not in result.data


async def test_multiple_workers_same_prompt(tmp_path: Path):
    """Test using multiple workers with the same prompt."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = pool.get_agent("main")
        worker = pool.get_agent("worker")
        specialist = pool.get_agent("specialist")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)
        assert isinstance(specialist, Agent)
        main_model = TestModel(call_tools=["ask_worker", "ask_specialist"])
        worker_model = TestModel(custom_output_text="I am a helpful worker assistant")
        specialist_model = TestModel(custom_output_text="I am a domain specialist")
        await main_agent.set_model(main_model)
        await worker.set_model(worker_model)
        await specialist.set_model(specialist_model)
        responses = []
        main_agent.message_sent.connect(lambda msg: responses.append(msg.content))
        await main_agent.run("Ask both workers: introduce yourselves")
        assert len(responses) > 0
        assert any("helpful worker" in r.lower() for r in responses)


@pytest.mark.integration
async def test_structured_worker_output(default_model: str):
    """Test that agents with BaseModel output    convert correctly when used as tools."""
    # Create structured agent and main agent that will use him as a tool
    structured = Agent(name="structured_agent", model=default_model, output_type=StructuredResponse)
    main_agent = Agent(name="main_agent", model=default_model)
    # Convert structured agent to tool and register with main agent
    tool = structured.to_tool()
    # Verify that return type annotation is set correctly
    assert tool.fn.__annotations__.get("return") == StructuredResponse
    main_agent.tools.register_tool(tool, enabled=True)
    # Test that both agents work together
    async with structured, main_agent:
        result = await main_agent.run("Ask structured_agent: return a message 'test' with value 42")
        tool_calls = result.get_tool_calls()
        assert len(tool_calls) > 0
        # Verify pydantic-ai properly converted the result to StructuredResponse
        structured_result = tool_calls[0].result
        assert isinstance(structured_result, StructuredResponse)
        assert structured_result.message
        assert structured_result.value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
