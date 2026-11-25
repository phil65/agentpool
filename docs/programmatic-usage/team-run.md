---
title: TeamRuns
description: Team execution and monitoring
icon: material/play
---

## What is a TeamRun?

A TeamRun represents a sequential execution pipeline of agents. Unlike Teams which define agent groups,
TeamRuns manage how agents process data in sequence, with each agent receiving the output of the previous one.
Optionally, a validator can be specified to create a final, structured output.

The key aspects are:

- Sequential processing order
- Message flow between agents
- Optional validation/structured output
- Execution monitoring
- Resource management

## Creating TeamRuns

### From Pool

```python
# Create execution pipeline from agent names
run = pool.create_team_run(["analyzer", "planner", "executor"])

# With validator for structured output
run = pool.create_team_run(
    ["analyzer", "planner"],
    validator=conclusion_writer
)
```

### Using Pipeline Operator

```python
# Create sequential pipeline from agents
pipeline = analyzer | planner | executor

def clean_data(msg: str) -> str:
    return f"Cleaned: {msg}"

# With function transformers
pipeline = analyzer | clean_data | planner | execute_plan

# Mixing teams
pipeline = analysis_team | planning_team | execution_team
```

## Running a TeamRun

### Direct Execution

Simple run that waits for completion:

```python
# Run and wait for results
results = await run.execute("Process this task")

# Access results
for response in results:
    print(f"{response.agent_name}: {response.message.content}")
```

### Monitored Execution

Get statistics while the run executes in background:

```python
# Start run and get stats object
stats = await run.run_in_background("Process this task")

# Wait for completion when needed
results = await run.wait()
```

For detailed monitoring capabilities, see [Task Monitoring](./task-monitoring.md).

## Iterating Over Execution

For fine-grained control, you can use `execute_iter()` which yields:

- Connection objects (`Talk`) before they're used
- Responses (`AgentResponse`) after each agent executes

This allows you to:

- Configure routing before messages flow
- Monitor individual results
- Handle errors per agent
- Transform messages between agents

Example:

```python
async for item in run.execute_iter("analyze this"):
    match item:
        case Talk():
            print(f"Next: {item.source.name} -> {item.target.name}")
            # Configure connection if needed
            item.transform = lambda msg: f"Previous: {msg.content}"
        case AgentResponse():
            if item.success:
                print(f"✅ {item.agent_name}: {item.message.content}")
            else:
                print(f"❌ {item.agent_name}: {item.error}")
                break
```

## Resource Management

TeamRuns handle cleanup automatically:

- Background tasks are tracked
- Connections are properly closed
- Resources are released on completion

You can also explicitly control the run:

```python
# Cancel execution
await run.cancel()

# Check status
if run.is_running:
    print("Still processing...")
```

### Properties and Status

```python
# Access team members
run.agents  # List of agents in the pipeline
run.name    # Team name (defaults to concatenated agent names)

# Check execution state
run.is_running  # Whether execution is active
await run.get_stats()  # Current execution statistics
```

## Team Statistics

The `stats` property provides aggregated information about the execution:

```python
stats = await run.get_stats()

# Basic information
print(f"Team: {stats.source_names} → {stats.target_names}")
print(f"Active connections: {stats.num_connections}")
print(f"Messages: {stats.message_count}")

# Cost tracking
print(f"Total tokens: {stats.token_count}")
print(f"Total cost: ${stats.total_cost:.4f}")
print(f"Bytes transferred: {stats.byte_count}")

# Message access
for msg in stats.messages:
    print(f"{msg.name}: {msg.content}")
```

## Running in Background

The base class provides background execution support:

```python
# Start execution
stats = await run.run_in_background("Process this")

# Wait for completion later
results = await run.wait()

# Or cancel if needed
await run.cancel()
```

## Resource Management

The base class handles:

- Task tracking and cleanup
- Connection management
- Error propagation
- Background task cancellation

This ensures proper cleanup even in error cases:

```python
try:
    stats = await run.run_in_background("Process this")
    # ... do other things ...
    results = await run.wait()
except Exception:
    # Background tasks are automatically cleaned up
    # Connections are properly closed
    await run.cancel()  # Explicit cancellation if needed
```

The combination of sequential processing and monitoring capabilities makes TeamRuns suitable for both simple pipelines and complex multi-agent workflows.
