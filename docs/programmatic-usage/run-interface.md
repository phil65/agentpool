---
title: Run Interface
description: Agent and team run interface
icon: lucide/play-circle
---

# Run Interface

The run interface provides a consistent way to interact with all message handlers (Agents, Teams, TeamRuns) in agentpool.
It serves both as a messaging protocol and a public API.

## Core Methods

### run()

Executes a prompt and returns a single ChatMessage:

```python

msg = await agent.run("analyze this")
msg = await team.run("analyze this")  # parallel execution
msg = await team_run.run("analyze this")  # sequential chain
```

### run.sync()

Synchronous convenience wrapper for `run()`:

```python
# Useful in sync contexts or notebooks
msg = agent.run.sync("analyze this")
msg = team.run.sync("analyze this")
msg = team_run.run.sync("analyze this")
```

### run_in_background()

Start execution in background and monitor progress:

```python
# Start execution
stats = await agent.run_in_background(
    "analyze this",
    max_count: int | None = None,  # Max number of runs
    interval: float = 1.0,  # Seconds between runs
    block: bool = False,  # Whether to block until completion
)

# Monitor execution
while agent.is_running:
    print(f"Messages processed: {stats.message_count}")
    await anyio.sleep(1)

# Cancel if needed
await agent.cancel()
```

### run_iter()

Asynchronously yields ChatMessages:

```python
async for msg in agent.run_iter(
    "analyze this",
    store_history=True,
    model="gpt-5",
):
    print(msg.content)
```

```

### run_stream()

Stream responses (supported by Agents and TeamRuns):

```python
async for event in agent.run_stream("analyze", model="gpt-5"):
    print(event)
```

Note: Parallel Teams don't support streaming as it wouldn't provide any benefit over run_iter().

## Advanced Usage

For advanced use cases requiring detailed execution information, stats tracking, or message flow intervention,
TeamRun provides an additional `execute_iter()` method:

```python
async for item in team_run.execute_iter("analyze"):
    match item:
        case Talk(source=source, targets=targets):
            print(f"Connection: {source.name} -> {targets[0].name}")
        case AgentResponse(agent_name=agent_name, message=message):
            print(f"Response from {agent_name}: {message.content}")
```
