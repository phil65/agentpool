"""Tests for Team execution."""

from __future__ import annotations

import pytest

from agentpool import Agent, AgentPool, ChatMessage, Team, TeamRun


async def test_team_parallel_execution():
    """Test that team runs all agents in parallel and collects responses."""
    async with AgentPool() as pool:
        # Create three agents that append their name to input
        a1 = Agent("a1", system_prompt="Append 'a1'", model="test")
        await pool.add_agent(a1)
        a2 = Agent("a2", system_prompt="Append 'a2'", model="test")
        await pool.add_agent(a2)
        a3 = Agent("a3", system_prompt="Append 'a3'", model="test")
        await pool.add_agent(a3)

        team = pool.create_team([a1, a2, a3])
        result = await team.execute("test")

        # Check that we got responses from all agents
        assert len(result) == 3
        agent_names = {r.agent_name for r in result}
        assert agent_names == {"a1", "a2", "a3"}

        # Check that stats were collected
        assert len(team.execution_stats.messages) == 3
        assert all(isinstance(msg, ChatMessage) for msg in team.execution_stats.messages)


async def test_team_shared_prompt():
    """Test that shared prompt is prepended to individual prompts."""
    async with AgentPool() as pool:
        # Create agents that echo their input
        def echo(prompt: str) -> str:
            return prompt

        a1 = pool.get_agent(Agent.from_callback(echo, name="a1"))
        a2 = pool.get_agent(Agent.from_callback(echo, name="a2"))

        # Create team with shared prompt
        team = pool.create_team([a1, a2], shared_prompt="Common instruction: ")
        result = await team.execute("specific task")

        # Each agent should get both prompts
        assert len(result) == 2
        for response in result:
            assert response.message
            assert "Common instruction" in str(response.message.content)
            assert "specific task" in str(response.message.content)


async def test_nested_teams():
    """Test nesting Teams and TeamRuns inside each other."""
    async with AgentPool() as pool:
        # Create basic agents
        a1 = Agent("a1", model="test")
        await pool.add_agent(a1)
        a2 = Agent("a2", model="test")
        await pool.add_agent(a2)
        a3 = Agent("a3", model="test")
        await pool.add_agent(a3)

        # Case 1: Team inside TeamRun
        team = Team([a1, a2])  # Team of two agents
        execution = TeamRun([team, a3])  # TeamRun with Team + Agent
        result = await execution.run("test message")
        assert isinstance(result, ChatMessage)
        # Team's messages should be in the chain
        assert len(execution.execution_stats.messages) == 2  # Team(a1+a2) + a3


async def test_nested_team_run():
    """Test nesting Teams and TeamRuns inside each other."""
    async with AgentPool() as pool:
        # Create basic agents
        a1 = Agent("a1", model="test")
        await pool.add_agent(a1)
        a2 = Agent("a2", model="test")
        await pool.add_agent(a2)
        a3 = Agent("a3", model="test")
        await pool.add_agent(a3)
        a4 = Agent("a4", model="test")
        await pool.add_agent(a4)

        # Case 2: TeamRun inside Team
        sequential = TeamRun([a1, a2])
        parallel_team = Team([sequential, a3, a4])  # Team containing TeamRun + Agents

        result = await parallel_team.run("test message")
        assert isinstance(result, ChatMessage)
        # Should have all messages
        assert len(parallel_team.execution_stats.messages) == 3  # TeamRun(a1+a2) + a3 + a4

        # # Test streaming with nested Team
        # async with execution.run_stream("test message") as stream:
        #     chunks = [chunk async for chunk in stream.stream_output()]
        #     assert chunks  # Should get chunks from all agents

        # Test iteration with nested TeamRun
        messages = [msg async for msg in parallel_team.run_iter("test message")]
        assert len(messages) == 3  # Should get all messages


async def test_simple_team_run_iter():
    """Test run_iter with a simple team of agents."""
    async with AgentPool() as pool:
        # Create basic agents
        a1 = Agent("a1", model="test")
        await pool.add_agent(a1)
        a2 = Agent("a2", model="test")
        await pool.add_agent(a2)

        # Simple parallel team
        team = pool.create_team([a1, a2])

        # Test iteration
        messages = [msg async for msg in team.run_iter("test message")]
        assert len(messages) == 2  # Should get one message per agent
        assert {msg.name for msg in messages} == {"a1", "a2"}


async def test_sequential_run_iter():
    """Test run_iter with a sequential execution (TeamRun)."""
    async with AgentPool() as pool:
        a1 = Agent("a1", model="test")
        await pool.add_agent(a1)
        a2 = Agent("a2", model="test")
        await pool.add_agent(a2)

        # Sequential execution
        sequential = TeamRun([a1, a2])

        messages = [msg async for msg in sequential.run_iter("test message")]
        assert len(messages) == 2
        # Should maintain order
        assert [msg.name for msg in messages] == ["a1", "a2"]


async def test_simple_team_with_teamrun_iter():
    """Test run_iter with a team containing a simple TeamRun."""
    async with AgentPool() as pool:
        a1 = Agent("a1", model="test")
        await pool.add_agent(a1)
        a2 = Agent("a2", model="test")
        await pool.add_agent(a2)
        a3 = Agent("a3", model="test")
        await pool.add_agent(a3)

        # Sequential execution as team member
        sequential = TeamRun([a1, a2])  # This is one unit
        # Team with two members: sequential and a3
        team = pool.create_team([sequential, a3])

        messages = [msg async for msg in team.run_iter("test message")]

        # Should get TWO messages: one from TeamRun, one from a3
        assert len(messages) == 2

        # Verify senders
        senders = {msg.name for msg in messages}
        assert senders == {sequential.name, "a3"}

        # Verify TeamRun message has metadata about its internal execution
        teamrun_msg = next(msg for msg in messages if msg.name == sequential.name)
        assert "execution_order" in teamrun_msg.metadata


async def test_team_run_iter_execution_order():
    """Test that run_iter preserves execution order within sequential parts."""
    async with AgentPool() as pool:
        a1 = Agent("a1", model="test")
        await pool.add_agent(a1)
        a2 = Agent("a2", model="test")
        await pool.add_agent(a2)
        a3 = Agent("a3", model="test")
        await pool.add_agent(a3)

        # Sequential execution
        sequential: TeamRun[None, str] = pool.create_team_run([a1, a2], name="sequential")
        # Team with sequential + single agent
        team = pool.create_team([sequential, a3], name="parallel")

        messages = [msg async for msg in team.run_iter("test message")]
        # Find sequential messages
        seq_msgs = [msg for msg in messages if msg.name == sequential.name]
        seq_msg = seq_msgs[0]
        # Order should be preserved within sequential execution
        assert [msg.name for msg in seq_msg.associated_messages] == ["a1", "a2"]


async def test_nodes_have_no_composition_operators():
    """Teams and pipelines are built explicitly: ``&`` and ``|`` are not defined."""
    a1, a2 = Agent("a1", model="test"), Agent("a2", model="test")
    team = Team([a1, a2])
    for left, right in [(a1, a2), (team, a1), (a1, team)]:
        with pytest.raises(TypeError):
            left & right  # type: ignore[operator]  # ty:ignore[unsupported-operator]
        with pytest.raises(TypeError):
            left | right  # type: ignore[operator]  # ty:ignore[unsupported-operator]


async def test_nested_team_and_team_run():
    """Teams and TeamRuns as members of each other."""
    async with AgentPool() as pool:
        a1, a2, a3, a4 = (Agent(f"a{i}", model="test") for i in range(1, 5))
        for agent in (a1, a2, a3, a4):
            await pool.add_agent(agent)
        team3 = Team([a1, a2])  # parallel
        seq3 = TeamRun([a3, a4])  # sequential

        # TeamRun with Team member
        combined_1 = TeamRun([team3, seq3])
        assert combined_1.nodes == [team3, seq3]

        # Team with TeamRun member
        combined_2 = Team([team3, seq3])
        assert combined_2.nodes == [team3, seq3]

        result = await combined_1.run("test")
        assert isinstance(result, ChatMessage)
        # All nodes should have executed
        assert len(combined_1.execution_stats.messages) == len(combined_1.nodes)

        result = await combined_2.run("test")
        assert isinstance(result, ChatMessage)
        assert len(combined_2.execution_stats.messages) == len(combined_2.nodes)


if __name__ == "__main__":
    pytest.main([__file__])
