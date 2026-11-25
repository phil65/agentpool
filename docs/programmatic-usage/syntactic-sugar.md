---
title: Syntactic Sugar
description: Syntactic conveniences and shortcuts
icon: material/code-block-braces
---

# Syntactic Sugar in AgentPool

AgentPool provides three operators for composing and connecting agents: `&`, `|`, and `>>`. These operators enable intuitive composition of agent workflows.

## Operators Overview

### `&` (AND) - Team Composition

Combines agents into a team that can work together in parallel or sequentially.

```python
# Create team from agents
team = analyzer & planner & executor

# Returns: Team[list[TDeps]] for same dependency types
# Returns: Team[list[Any]] for mixed dependency types
# Returns: Team[None] if both sides have no dependencies
```

When combining with `&`:

- Agents become part of the same team
- System prompts are combined with line breaks
- Dependencies are collected into a list (if present)
- Team capabilities are merged

### `|` (OR) - Pipeline Creation

Creates sequential processing pipelines where output flows from one agent to the next.

```python
# Create pipeline with agents and transforms
pipeline = agent1 | transform | agent2

# Callables are automatically converted to agents
def uppercase(text: str) -> str:
    return text.upper()

pipeline = agent1 | uppercase | agent2

# Returns: TeamRun[TDeps] with sequential execution mode
```

When creating pipelines with `|`:

- Each component processes the output of the previous one
- Results flow through the pipeline in order
- Monitoring is automatically set up

### `>>` (Forward) - Message Routing

Sets up message forwarding between agents.

```python
# Forward results from one agent to another
agent1 >> agent2  # agent2 receives agent1's outputs

# Forward to multiple targets
agent1 >> (agent2 & agent3)  # Both receive outputs

# Returns: Talk for single target
# Returns: TeamTalk for multiple targets
```

When setting up forwarding with `>>`:

- Messages are forwarded automatically
- Original agent continues independently
- Multiple targets receive the same messages
- No automatic response waiting

## Combining Operators

The operators can be combined for complex workflows:

```python
# Create team and forward to processor
(analyzer & researcher) >> processor

# Create pipeline with team in middle
input_agent | (analyzer & researcher) | output_agent

# Complex routing
agent1 >> (transformer | (agent2 & agent3))
```

## Type Safety

The operators maintain type safety where possible:

```python
# Same dependency types
agent1 = Agent[ConfigDeps]()
agent2 = Agent[ConfigDeps]()
team = agent1 & agent2  # Team[list[ConfigDeps]]

# Mixed dependency types
agent3 = Agent[OtherDeps]()
mixed = agent1 & agent3  # Team[list[Any]]

# No dependencies
agent4 = Agent()
agent5 = Agent()
clean = agent4 & agent5  # Team[None]
```

## Shared Team Context

When combining agents into a team, each agent maintains its own system prompts. The team's `shared_prompt` is an additional instruction layer that applies to team operations:

```python
agent1 = Agent(system_prompt="You are an analyzer.")
agent2 = Agent(system_prompt="You are a summarizer.")

# Create team with shared instructions
team = Team(
    agents=[agent1, agent2],
    shared_prompt="Some information both agents should know."
)

# When combining teams, shared prompts are combined:
team1 = Team([agent1], shared_prompt="Focus on technical aspects")
team2 = Team([agent2], shared_prompt="Ensure clarity")

combined = team1 & team2
print(combined.shared_prompt)
# Output:
# Focus on technical aspects
# Ensure clarity

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

Common patterns using the operators:

```python
# Analysis pipeline with transforms
analyzer = Agent(name="analyzer")
def clean_text(text: str) -> str:
    return text.strip().lower()

pipeline = analyzer | clean_text | summarizer

# Parallel processing with forwarding
processors = agent1 & agent2 & agent3
processors >> collector

# Complex workflow
input_agent | (analyzer & researcher) | formatter >> storage
```
