---
title: Team
description: Working with agent teams and groups
icon: material/account-group
---

## Overview

Teams in AgentPool allow you to organize and orchestrate multiple agents as a group. Teams provide:

- Parallel and sequential execution
- Shared contexts and prompts
- Controlled agent communication
- Compatibility with agent routing patterns
- Chain execution capabilities

## Creating Teams

### Direct Creation

```python
from agentpool.delegation import Team

team = Team(
    agents=[agent1, agent2, agent3],
    shared_prompt="Team objective",
    name="analysis_team"
)
```

### Using Pool Helper

```python
# Create from agent names
team = pool.create_team(
    agents=["analyzer", "planner", "executor"],
    shared_prompt="Team objective",        # Common prompt
)
```

## Team Execution Patterns

### Parallel Execution

All team members work simultaneously:

```python
# Run all agents in parallel
team_response = await team.execute(prompt="Analyze this code")

# Access results
for response in team_response:
    print(f"{response.agent_name}: {response.message.content}")

# Check for errors
for agent_name, error in team_response.errors.items():
    print(f"{agent_name} failed: {error}")
```

### Sequential Execution

For sequential execution, use `TeamRun` instead:

```python
from agentpool import TeamRun

# Create sequential pipeline
run = TeamRun([agent1, agent2, agent3])

# Execute sequentially
result = await run.run("Review this PR")
```

### Streaming Results

Stream messages as they arrive from parallel execution:

```python
# Yield messages as agents complete
async for msg in team.run_iter("Analyze this"):
    print(f"Result: {msg.content}")

# Or stream events
async for event in team.run_stream("Analyze this"):
    print(event)
```

## Team as a Target Container

Teams are compatible with AgentPool's routing patterns:

```python
# Forward results from an agent to team
agent >> team  # All team members receive results

# Forward team results to another agent
team >> other_agent

# Create complex routing
Team([*team1.nodes, *team2.nodes]) >> coordinator  # Union of teams

# Using connect_to
agent.connect_to(
    team,
    connection_type="run",    # How to handle messages
    delay=timedelta(seconds=1)  # Optional delay
)
```

## Team Distribution and Knowledge Sharing

## Team Response Handling

Teams provide rich response objects:

```python
team_response = await team.execute(prompt)

# Access errors
for name, error in team_response.errors.items():
    print(f"{name} failed: {error}")

# Timing information
print(f"Total duration: {team_response.duration}")

# Access individual responses
for response in team_response:
    print(f"{response.agent_name}: {response.message.content}")
```

## Team Composition

Teams are nodes, so they can be members of other teams:

```python
# Create combined team
analysis_team = Team([analyzer, researcher, summarizer])

# A team of teams, or one team with all their members
full_team = Team([analysis_team, review_team])
flat_team = Team([*analysis_team.nodes, *review_team.nodes])

# Create groups inline
Team([analyzer, researcher]) >> reviewer
```

## Routing Control

Fine-tune how messages flow through the team:

```python
# Connect with specific settings
team.connect_to(
    target,
    connection_type="context",  # Add as context
    delay=timedelta(minutes=1) # Delayed execution
)

# Get communication stats
stats = team.connections.stats
print(f"Messages handled: {stats.message_count}")
print(f"Total tokens: {stats.token_count}")
```

## Automatic Team Member Selection

Teams can use a picker agent to automatically select appropriate team members based
on their descriptions.

```python
# Team members with clear descriptions of their capabilities
developer = Agent(
    name="developer",
    description="Implements code changes and new features in Python",
    system_prompt="You write Python code..."  # System prompt is separate!
)

doc_writer = Agent(
    name="doc_writer",
    description="Writes and updates technical documentation and README files",
    system_prompt="You write documentation...",
    ...
)

lazy_bob = Agent(
    name="lazy_bob",
    description="Has no useful skills or contributions",
    system_prompt="You avoid work...",
    ...
)

# Picker agent
coordinator = Agent(
    name="coordinator",
    system_prompt="You assign work to team members based on their descriptions.",
    ...
)
```

### Action Filtering (Parallel Team)

In parallel teams, the picker selects which team members should work on the current task
by matching task requirements against agent descriptions:

```python
feature_team = Team(
    [developer, doc_writer, lazy_bob],
    picker=coordinator,
    num_picks=None,  # Auto - let coordinator decide how many based on descriptions
)

# Coordinator sees task and agent descriptions:
# - "Implements code changes..." -> selected for coding task
# - "Writes and updates documentation..." -> selected for docs task
# - "Has no useful skills..." -> not selected
await feature_team.run(
    "Implement a new CLI flag and document it in README"
)
```

### Step Filtering (Sequential Team)

In sequential teams, the picker selects which team member should handle each step
by matching step requirements against agent descriptions:

```python
bugfix_team = TeamRun(
    [
        coder := Agent(
            name="coder",
            description="Implements bug fixes and code improvements"
        ),
        tester := Agent(
            name="tester",
            description="Runs tests and verifies code changes"
        ),
        lazy_jim := Agent(
            name="lazy_jim",
            description="Contributes nothing to the team"
        )
    ],
    picker=coordinator,
    num_picks=1,  # One agent per step
)

# For each step, coordinator matches requirements to descriptions:
# 1. "Implements bug fixes..." -> selected for fix
# 2. "Runs tests..." -> selected for verification
# "Contributes nothing..." -> never selected
await bugfix_team.run(
    "Fix and verify the login system bug"
)
```

### Key Points

- Agent descriptions are crucial for selection
- System prompts are separate from descriptions
- Picker uses descriptions to match agents to tasks
- Selection can be automatic (num_picks=None) or fixed (num_picks=N)

### Picker Configuration

Both team types support:

- `picker`: Agent that selects team members
- `num_picks`: Number of agents to select
  - `None`: Auto mode - picker decides how many needed
  - `1`: Single agent per task/step
  - `N`: Exact number of agents required

## Best Practices

1. **Team Size**: Keep teams focused and reasonably sized
2. **Error Handling**: Use `require_all` appropriately for chains
3. **Resources**: Share common resources across team members
4. **Monitoring**: Use response objects for execution monitoring
5. **Composition**: Use team composition for complex workflows

## Common Patterns

### Analysis Pipeline

```python
# Create specialized team
analysis_team = pool.create_team(
    ["analyzer", "reviewer", "summarizer"],
)

# Connect to result handler
analysis_team >> result_collector

# Run analysis
await analysis_team.execute("Analyze this code")
```

### Team Task information

```python
team_response = await team.execute(prompt)

print(f"Execution time: {team_response.duration}")
print(f"Errors: {len(team_response.errors)}")

for agent_name, error in team_response.errors.items():
    print(f"Error in {agent_name}: {error}")
```
