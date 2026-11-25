---
title: Task Monitoring
description: Advanced task monitoring features
icon: material/pulse
---

# Team Execution and Monitoring

AgentPool provides two main classes for team operations:

- `Team`: A group of agents that can work together
- `TeamRun`: A sequential execution pipeline of agents

This guide focuses on execution monitoring and progress tracking.

## Basic Team Operation

Teams can be created and connected in different ways:

```python
# Create team from agents
team = Team([agent1, agent2, agent3])

# Create team through pool
team = pool.create_team(["analyzer", "planner", "executor"])

# Connect team to target
team >> target_agent  # All team members forward to target
```

## Sequential Execution with TeamRun

For monitored sequential processing, use TeamRun:

```python
# Create execution pipeline
run = agent1 | agent2 | agent3  # Direct creation
# or
run = pool.create_team_run(["analyzer", "planner", "executor"])
```

### Monitoring Execution

TeamRun provides built-in monitoring through its background execution:

```python
# Start execution in background
stats = await run.run_in_background("Task to execute")

# Check current status while running
while run.is_running:
    print(f"Active connections: {len(stats)}")
    print(f"Messages processed: {len(stats.messages)}")
    print(f"Errors: {len(stats.errors)}")
    await anyio.sleep(0.5)

# Wait for completion and get results
result = await run.wait()
```

### The Stats Object

The AggregatedTalkStats object provides execution metrics and history:

```python
# Access during or after run
stats = await run.get_stats()

# Message information
stats.messages         # All messages exchanged
stats.message_count   # Total message count
stats.tool_calls      # Tool calls made

# Timing and costs
stats.token_count    # Total tokens used
stats.total_cost     # Total cost in USD
stats.byte_count     # Raw data transferred

# Agent information
stats.source_names   # Agents that sent messages
stats.target_names   # Agents that received messages
```

### Example: Monitored Execution

```python
from agentpool import AgentPool

async def main():
    async with AgentPool() as pool:
        # Create three agents with different roles
        analyzer = Agent(model="xy", name="analyzer", system_prompt="You analyze text and find key points.")
        await pool.add_agent(analyzer)
        summarizer = ClaudeCodeAgent(name="summarizer", system_prompt="You create concise summaries.")
        await pool.add_agent(summarizer)
        critic = CodexAgent(name="critic", system_prompt="You evaluate and critique summaries.")
        await pool.add_agent(critic)
        # Create execution pipeline
        run = agent1 | agent2 | agent3

        # Start run and get stats object
        stats = await run.run_in_background("Process this text...")

        # Monitor progress
        while run.is_running:
            print("\nCurrent status:")
            print(f"Processed messages: {stats.message_count}")
            print(f"Active connections: {stats.num_connections}")
            await anyio.sleep(0.5)

        # Wait for completion
        result = await run.wait()

        # Final statistics
        print(f"\nExecution complete:")
        print(f"Total cost: ${stats.total_cost:.4f}")
        print(f"Total tokens: {stats.token_count}")
```

### Resource Management

Both Team and TeamRun handle cleanup automatically:

- Background tasks are tracked and cleaned up
- Connections are properly closed
- Resources are released on completion

## Summary

The current monitoring system provides:

- Real-time execution tracking
- Comprehensive statistics
- Error logging
- Cost tracking
- Resource management

All while maintaining clean separation between team organization (Team) and sequential execution (TeamRun).
