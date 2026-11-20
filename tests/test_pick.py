from __future__ import annotations

import pytest

from agentpool import Agent


@pytest.mark.integration
async def test_pick_from_options(default_model: str):
    """Test picking from a list of options."""
    # Create agent for making decisions
    sys_prompt = "You are an expert at making clear decisions."
    decider = Agent(model=default_model, system_prompt=sys_prompt)
    options = ["A", "B", "C"]  # Test picking from simple options
    decision = await decider.talk.pick(options, task="Pick A and give a random reason!")
    assert decision.selection in options
    assert len(decision.reason) > 0


@pytest.mark.integration
@pytest.mark.flaky(reruns=2)
async def test_pick_from_agents(default_model: str):
    """Test picking from a team of agents."""
    # Create a team of specialized agents
    desc = "Specializes in code analysis and best practices"
    analyzer = Agent(name="code_analyzer", model=default_model, description=desc)
    desc = "Focuses on security vulnerabilities"
    reviewer = Agent(name="security_expert", model=default_model, description=desc)
    team = [analyzer, reviewer]
    sys_prompt = "You are an expert at delegating tasks."
    decider = Agent(model=default_model, system_prompt=sys_prompt)
    # Test agent selection
    task = "We found potential SQL injection vulnerabilities. Who should investigate?"
    decision = await decider.talk.pick(team, task=task)
    assert decision.selection in team
    assert decision.selection.name == "security_expert"  # Should pick security expert
    assert "security" in decision.reason.lower()


@pytest.mark.integration
async def test_pick_multiple(default_model: str):
    """Test picking multiple options with constraints."""
    decider = Agent(model=default_model)
    decision = await decider.talk.pick_multiple(
        ["A", "B", "C"],
        task="Pick A and B. Always pick both!.",
        min_picks=2,
        max_picks=2,
    )
    assert decision.selections == ["A", "B"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
