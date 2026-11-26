---
title: Syntactic Sugar
description: Syntactic conveniences and shortcuts
icon: material/code-block-braces
---

# Syntactic Sugar in AgentPool

Teams and pipelines are built explicitly, with `Team` and `TeamRun`; connections have
one shortcut, the `>>` operator (`connect_to`).

## Teams and Pipelines

```python
from agentpool import Agent, Team, TeamRun

# A team: its members work in parallel
team = Team([analyzer, planner, executor])

# A pipeline: output flows from one node to the next
pipeline = TeamRun([agent1, agent2])

# Functions become agents with Agent.from_callback
def uppercase(text: str) -> str:
    return text.upper()

pipeline = TeamRun([agent1, Agent.from_callback(uppercase), agent2])
```

Teams and pipelines are nodes themselves, so they nest:

```python
analysis = Team([analyzer, researcher])
workflow = TeamRun([input_agent, analysis, output_agent])
```

## `>>` (Forward) - Message Routing

Sets up message forwarding between nodes, like `connect_to`.

```python
# Forward results from one agent to another
agent1 >> agent2  # agent2 receives agent1's outputs

# Forward to several targets (one connection each)
agent1 >> [agent2, agent3]

# Forward to a team
agent1 >> Team([agent2, agent3])

# Returns: Talk for a single target
# Returns: TeamTalk for several targets
```

When setting up forwarding with `>>`:

- Messages are forwarded automatically
- Original agent continues independently
- Multiple targets receive the same messages
- No automatic response waiting

Options (filters, queueing, `parallel=True` for several targets, ...) need
`connect_to`; see [Connections](../advanced/connections.md).

## Shared Team Context

When combining agents into a team, each agent maintains its own system prompts. The team's `shared_prompt` is an additional instruction layer that applies to team operations:

```python
agent1 = Agent(system_prompt="You are an analyzer.")
agent2 = Agent(system_prompt="You are a summarizer.")

# Create team with shared instructions
team = Team(
    [agent1, agent2],
    shared_prompt="Some information both agents should know."
)

# Individual agent system prompts remain unchanged
print(agent1.sys_prompts.prompts)  # ["You are an analyzer."]
print(agent2.sys_prompts.prompts)  # ["You are a summarizer."]
```

The shared prompt provides team-level instructions without modifying individual agent behaviors. This is useful for:

- Coordinating team efforts
- Setting shared goals
- Providing context for collaboration
- Guiding multi-agent interactions

## Usage Examples

```python
# Analysis pipeline with a transform
def clean_text(text: str) -> str:
    return text.strip().lower()

pipeline = TeamRun([analyzer, Agent.from_callback(clean_text), summarizer])

# Parallel processing with forwarding
processors = Team([agent1, agent2, agent3])
processors >> collector

# A workflow whose result goes to storage
workflow = TeamRun([input_agent, Team([analyzer, researcher]), formatter])
workflow >> storage
```
