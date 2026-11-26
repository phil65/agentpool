import pytest

from agentpool import Agent


async def test_conversation_history_management():
    """Test that conversation history is maintained correctly."""
    async with Agent(model="test") as agent:
        # Send first message and check basic history
        await agent.run("First message")
        history1 = agent.conversation.get_history()
        assert len(history1) == 2  # User message + Response

        # Send second message and verify history includes both exchanges
        await agent.run("Second message")
        history2 = agent.conversation.get_history()
        assert len(history2) == 4  # Both exchanges

        # Verify messages are in correct order
        assert "First message" in str(history2[0])
        assert "Second message" in str(history2[2])

        # Test history clearing
        await agent.conversation.clear()
        assert len(agent.conversation.get_history()) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
