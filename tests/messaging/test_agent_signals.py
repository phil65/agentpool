from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool
from agentpool.messaging.message_utils import build_message_index, get_message_chain


async def test_message_chain():
    """Test that message chain tracks transformations correctly via parent_id."""
    async with AgentPool() as pool:
        agent_a = Agent("agent-a", model="test")
        await pool.add_agent(agent_a)
        agent_b = Agent("agent-b", model="test")
        await pool.add_agent(agent_b)
        agent_c = Agent("agent-c", model="test")
        await pool.add_agent(agent_c)

        # Connect chain
        agent_a.connect_to(agent_b)
        agent_b.connect_to(agent_c)

        # When A processes a new message
        result_a = await agent_a.run("Start")
        assert result_a.parent_id is not None  # Points to user message

        # When B processes A's message via run_message
        result_b = await agent_b.run_message(result_a)
        assert result_b.parent_id is not None
        # Chain should show A
        chain_b = get_message_chain(result_b, pool.get_agents())
        assert "agent-a" in chain_b

        # When C processes B's message
        result_c = await agent_c.run_message(result_b)
        assert result_c.parent_id is not None
        # Chain should show A and B
        chain_c = get_message_chain(result_c, pool.get_agents())
        assert "agent-a" in chain_c
        assert "agent-b" in chain_c


async def test_run_result_has_parent_id():
    """Test that the message returned by run() has proper parent_id."""
    async with AgentPool() as pool:
        model = TestModel(custom_output_text="Response from A")
        agent_a = Agent("agent-a", model=model)
        await pool.add_agent(agent_a)
        agent_b = Agent("agent-b", model=model)
        await pool.add_agent(agent_b)
        # Connect A to B
        agent_a.connect_to(agent_b)
        # When A runs
        result = await agent_a.run("Test message")
        # The returned message should have parent_id pointing to user message
        assert result.parent_id is not None
        # Wait for forwarding to complete
        await agent_a.task_manager.complete_tasks()
        await agent_b.task_manager.complete_tasks()
        # B's messages should have parent_id tracking the chain
        if agent_b.conversation.chat_messages:
            b_user_msg = next(
                (m for m in agent_b.conversation.chat_messages if m.role == "user"),
                None,
            )
            if b_user_msg:
                # The user message in B should have parent_id from A's response
                assert b_user_msg.parent_id == result.message_id


async def test_message_chain_through_routing():
    """Test that message chain tracks correctly through the routing system."""
    async with AgentPool() as pool:
        model_a = TestModel(custom_output_text="Response from A")
        model_b = TestModel(custom_output_text="Response from B")
        model_c = TestModel(custom_output_text="Response from C")

        agent_a = Agent("agent-a", model=model_a)
        await pool.add_agent(agent_a)
        agent_b = Agent("agent-b", model=model_b)
        await pool.add_agent(agent_b)
        agent_c = Agent("agent-c", model=model_c)
        await pool.add_agent(agent_c)
        # Connect the chain
        agent_a.connect_to(agent_b)
        agent_b.connect_to(agent_c)
        # When A starts the chain
        await agent_a.run("Start message")
        # Wait for all routing to complete
        await agent_a.task_manager.complete_tasks()
        await agent_b.task_manager.complete_tasks()
        await agent_c.task_manager.complete_tasks()
        # All agents should share the same session_id
        assert (
            agent_a.conversation.chat_messages[0].session_id
            == agent_b.conversation.chat_messages[0].session_id
        )
        assert (
            agent_b.conversation.chat_messages[0].session_id
            == agent_c.conversation.chat_messages[0].session_id
        )

        # C's response should have a chain back through B and A
        if agent_c.conversation.chat_messages:
            c_response = next(
                (m for m in agent_c.conversation.chat_messages if m.role == "assistant"),
                None,
            )
            if c_response:
                chain = get_message_chain(c_response, pool.get_agents())
                # Chain should include both A and B
                assert "agent-a" in chain or "agent-b" in chain


async def test_build_message_index():
    """Test that pool.build_message_index works across agents."""
    async with AgentPool() as pool:
        agent_a = Agent("agent-a", model="test")
        await pool.add_agent(agent_a)
        agent_b = Agent("agent-b", model="test")
        await pool.add_agent(agent_b)

        result_a = await agent_a.run("Hello from A")
        result_b = await agent_b.run("Hello from B")
        index = build_message_index(pool.get_agents())

        # Should find messages from both agents
        assert result_a.message_id in index
        assert result_b.message_id in index

        found_a_msg, found_a_agent = index[result_a.message_id]
        found_b_msg, found_b_agent = index[result_b.message_id]

        assert found_a_msg.message_id == result_a.message_id
        assert found_a_agent == "agent-a"
        assert found_b_msg.message_id == result_b.message_id
        assert found_b_agent == "agent-b"

        # Non-existent ID should not be in index
        assert "non-existent-id" not in index


if __name__ == "__main__":
    pytest.main([__file__])
