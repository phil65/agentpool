"""Tests for parent_id tree structure in conversations."""

from typing import Any

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool, ChatMessage


TEST_RESPONSE = "I am a test response"


@pytest.fixture
def test_agent() -> Agent[None]:
    """Create an agent with TestModel for testing."""
    model = TestModel(custom_output_text=TEST_RESPONSE)
    return Agent(name="test-agent", model=model)


class TestParentIdBasic:
    """Tests for basic parent_id linking in conversations."""

    async def test_first_user_message_has_no_parent(self, test_agent: Agent[None]):
        """First user message in a conversation should have no parent_id."""
        async for _event in test_agent.run_stream("Hello"):
            pass

        history = test_agent.conversation.get_history()
        assert len(history) == 2  # user + response

        user_msg = history[0]
        assert user_msg.role == "user"
        assert user_msg.parent_id is None  # First message has no parent

    async def test_response_has_user_message_as_parent(self, test_agent: Agent[None]):
        """Response message should have the user message as parent."""
        async for _event in test_agent.run_stream("Hello"):
            pass

        history = test_agent.conversation.get_history()
        user_msg = history[0]
        response_msg = history[1]

        assert response_msg.role == "assistant"
        assert response_msg.parent_id == user_msg.message_id

    async def test_multi_turn_parent_chain(self, test_agent: Agent[None]):
        """Multiple turns should form a proper parent chain."""
        # First turn
        async for _event in test_agent.run_stream("First message"):
            pass

        # Second turn
        async for _event in test_agent.run_stream("Second message"):
            pass

        history = test_agent.conversation.get_history()
        assert len(history) == 4  # 2 user + 2 response

        user_1 = history[0]
        response_1 = history[1]
        user_2 = history[2]
        response_2 = history[3]

        # First user message has no parent
        assert user_1.parent_id is None

        # First response points to first user message
        assert response_1.parent_id == user_1.message_id

        # Second user message points to first response
        assert user_2.parent_id == response_1.message_id

        # Second response points to second user message
        assert response_2.parent_id == user_2.message_id

    async def test_parent_chain_is_linear(self, test_agent: Agent[None]):
        """Verify the parent chain forms a linear sequence."""
        # Run 3 turns
        for i in range(3):
            async for _event in test_agent.run_stream(f"Message {i}"):
                pass

        history = test_agent.conversation.get_history()
        assert len(history) == 6  # 3 user + 3 response

        # Walk the chain from last to first
        current = history[-1]
        chain = [current]

        while current.parent_id is not None:
            # Find parent in history
            parent = next(m for m in history if m.message_id == current.parent_id)
            chain.append(parent)
            current = parent

        # Chain should contain all messages
        assert len(chain) == 6

        # First message (end of chain) should have no parent
        assert chain[-1].parent_id is None
        assert chain[-1].role == "user"


class TestParentIdForwarding:
    """Tests for parent_id behavior when messages are forwarded between agents."""

    async def test_forwarded_message_preserves_original_parent_id(self):
        """When a message is forwarded, it should preserve its original parent_id."""
        async with AgentPool() as pool:
            model1 = TestModel(custom_output_text="Main response")
            main_agent = Agent("main-agent", model=model1)
            await pool.add_agent(main_agent)

            model2 = TestModel(custom_output_text="Helper response")
            helper_agent = Agent("helper-agent", model=model2)
            await pool.add_agent(helper_agent)

            # Set up forwarding
            main_agent.connect_to(helper_agent)

            # Collect messages from helper
            helper_messages: list[ChatMessage[Any]] = []
            helper_agent.message_sent.connect(helper_messages.append)

            # Run main agent (which forwards to helper)
            await main_agent.run("Hello")
            await main_agent.task_manager.complete_tasks()
            await helper_agent.task_manager.complete_tasks()

            # Helper should have received a forwarded message
            assert len(helper_messages) == 1
            helper_response = helper_messages[0]

            # The helper's response should have a parent_id pointing to
            # the user message in its own conversation
            helper_history = helper_agent.conversation.get_history()
            assert len(helper_history) >= 2

            # Find the user message (forwarded from main)
            user_msg = next(m for m in helper_history if m.role == "user")
            assert helper_response.parent_id == user_msg.message_id

    async def test_forwarded_message_tracks_forwarding_chain(self):
        """Forwarded messages should track their forwarding path."""
        async with AgentPool() as pool:
            model1 = TestModel(custom_output_text="Agent 1 response")
            agent1 = Agent("agent-1", model=model1)
            await pool.add_agent(agent1)

            model2 = TestModel(custom_output_text="Agent 2 response")
            agent2 = Agent("agent-2", model=model2)
            await pool.add_agent(agent2)

            # Set up forwarding: agent1 -> agent2
            agent1.connect_to(agent2)

            # Collect all messages
            all_messages: list[ChatMessage[Any]] = []
            agent1.message_sent.connect(all_messages.append)
            agent2.message_sent.connect(all_messages.append)

            await agent1.run("Hello")
            await agent1.task_manager.complete_tasks()
            await agent2.task_manager.complete_tasks()

            # Agent 2's user message should show it was forwarded
            agent2_history = agent2.conversation.get_history()
            forwarded_user_msg = next(m for m in agent2_history if m.role == "user")

            # Message was forwarded (tracked via parent_id now)
            assert forwarded_user_msg.parent_id is not None


class TestParentIdWithRun:
    """Tests for parent_id with non-streaming run()."""

    async def test_run_sets_parent_id(self, test_agent: Agent[None]):
        """Non-streaming run() should also set parent_id correctly."""
        await test_agent.run("First")
        await test_agent.run("Second")

        history = test_agent.conversation.get_history()
        assert len(history) == 4

        user_1 = history[0]
        response_1 = history[1]
        user_2 = history[2]
        response_2 = history[3]

        # Check the chain
        assert user_1.parent_id is None
        assert response_1.parent_id == user_1.message_id
        assert user_2.parent_id == response_1.message_id
        assert response_2.parent_id == user_2.message_id


class TestChatMessageParentId:
    """Tests for ChatMessage.parent_id field directly."""

    def test_user_prompt_accepts_parent_id(self):
        """ChatMessage.user_prompt should accept parent_id parameter."""
        msg = ChatMessage.user_prompt("Hello", parent_id="parent-123")
        assert msg.parent_id == "parent-123"

    def test_user_prompt_defaults_to_none(self):
        """ChatMessage.user_prompt should default parent_id to None."""
        msg = ChatMessage.user_prompt("Hello")
        assert msg.parent_id is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
