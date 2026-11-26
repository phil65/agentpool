"""Tests for AgentPool functionality."""

from typing import TYPE_CHECKING

from pydantic import BaseModel
import pytest

from agentpool import AgentPool, AgentsManifest


if TYPE_CHECKING:
    from agentpool import ChatMessage


class ConversationOutput(BaseModel):
    """Test output for conversation flow."""

    message: str
    conversation_index: int


def make_response(prompt: str) -> ConversationOutput:
    """Callback that tracks conversation order."""
    # Track what message we're on in the conversation
    make_response.count = getattr(make_response, "count", 0) + 1  # type: ignore
    return ConversationOutput(
        message=f"Response to: {prompt}",
        conversation_index=make_response.count,  # type: ignore
    )


TEST_CONFIG = f"""\
responses:
  ConversationOutput:
    response_schema:
        type: inline
        description: Output with conversation tracking
        fields:
            message:
                type: str
                description: Response message
            conversation_index:
                type: int
                description: Position in conversation

agents:
  test_agent:
    type: native
    display_name: Test Agent
    description: Agent for testing conversation flow
    model:
      type: function
      function: {__name__}.make_response
    output_type: ConversationOutput
    system_prompt: You are a test agent

  error_agent:
    display_name: Error Agent
    description: Agent that always raises errors
    model: test
    system_prompt: You are an error agent
"""


async def test_agent_pool_conversation_flow():
    """Test conversation flow maintaining history between messages."""
    manifest = AgentsManifest.from_yaml(TEST_CONFIG)

    async with AgentPool(manifest) as pool:
        # Get agent directly for conversation
        agent = pool.get_agent("test_agent", output_type=ConversationOutput)

        # Run multiple prompts in sequence
        responses: list[ChatMessage[ConversationOutput]] = []
        prompts = ["Hello!", "How are you?"]

        for prompt in prompts:
            result = await agent.run(prompt)
            responses.append(result)

        # Verify correct number of responses
        assert len(responses) == 2

        # Verify conversation order was maintained
        assert responses[0].data.conversation_index == 1
        assert responses[1].data.conversation_index == 2
        print(responses)
        # Verify message content
        assert responses[0].data.message == "Response to: Hello!"
        assert responses[1].data.message == "Response to: How are you?"

        # Verify agent name
        assert all(r.name == "test_agent" for r in responses)


async def test_agent_pool_validation():
    """Test AgentPool validation and error handling."""
    manifest = AgentsManifest.from_yaml(TEST_CONFIG)

    # Test getting non-existent agent
    async with AgentPool(manifest) as pool:
        with pytest.raises(KeyError, match="nonexistent"):
            pool.get_agent("nonexistent")


async def test_agent_pool_team_errors():
    """Test error handling in team tasks."""
    manifest = AgentsManifest.from_yaml(TEST_CONFIG)

    async with AgentPool(manifest) as pool:
        # Test with non-existent team member
        with pytest.raises(KeyError, match="nonexistent"):
            pool.create_team([pool.get_agent("test_agent"), pool.get_agent("nonexistent")])


async def test_agent_pool_cleanup():
    """Test proper cleanup of agent resources."""
    manifest = AgentsManifest.from_yaml(TEST_CONFIG)

    # Use context manager to ensure proper cleanup
    async with AgentPool(manifest) as pool:
        # Add some agents
        _agent = pool.get_agent("test_agent")
        assert "test_agent" in pool.get_agents()

        await pool.cleanup()
        assert not pool.get_agents()  # Should be empty after cleanup
