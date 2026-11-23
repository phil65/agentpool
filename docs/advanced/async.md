---
title: Async Operations
description: Asynchronous operations and patterns
icon: material/sync
---

# Asynchronous Operations in AgentPool

AgentPool is built with asyncio at its core to handle concurrent operations efficiently. This guide explains the async patterns and features in the library.

## Basic Usage

```python
# Single agent
async with Agent(...) as agent:
    result = await agent.run("What can you do?")
    print(result.content)

# Multiple agents in a pool
async with AgentPool("agents.yml") as pool:
    analyzer = pool.get_agent("analyzer")
    summarizer = pool.get_agent("summarizer")

    # Run agents concurrently
    results = await asyncio.gather(
        analyzer.run("Analyze this text"),
        summarizer.run("Summarize this text")
    )
```

## Agent Connections and Waiting

When agents are connected, messages flow between them asynchronously:

```python
# Messages flow automatically
analyzer >> summarizer  # analyzer's outputs go to summarizer

# The summarizer runs as analyzer's turn ends; analyzer.run() returns after it
await analyzer.run("Analyze")

# A connection with a delay runs in the background instead
talk = analyzer.connect_to(summarizer, delay=timedelta(seconds=5))
await analyzer.run("Analyze")  # returns before the summarizer ran

# Make analyzer's runs wait for that background work (and what it routes on)
analyzer.connections.set_wait_state(summarizer)

# Or wait for it once, without the flag
await analyzer.connections.wait_for_connections()
```

Waiting is a setting of the connection, not of a run. In YAML it is `wait_for_completion`
on a connection.

## Parallel Initialization

Both Agent and AgentPool support parallel initialization of their components:

```python
# Pool initialization (default: True)
pool = AgentPool(
    "agents.yml",
    parallel_load=True  # agents initialize concurrently
)

# Agent initialization (default: True)
agent = Agent(
    "config.yml",
    parallel_init=True  # components initialize concurrently
)
```

### What Gets Loaded in Parallel

For an Agent, these components can initialize concurrently:

- Runtime configuration and tools
- Event system setup
- MCP server connections
- Knowledge sources:
  - File resources
  - URLs
  - Dynamic prompts
  - External resources

For an AgentPool:

- All agents' async contexts
- Each agent's individual components

## Performance Considerations

Parallel initialization can significantly speed up startup when you have:

- Multiple agents in a pool
- Multiple agents using MCP servers
- Many knowledge sources
- External resources to load

## Background Operations

Some operations can run in the background while maintaining async safety:

```python
# Continuous background processing
await await agent.run_in_background("Monitor this", interval=5.0)

# Stop background processing
await agent.stop()
```

## Event-Based Operations

AgentPool supports event-driven agent operation through file watchers and other triggers. The simplest way to start event-based mode is through the CLI:

```bash
agentpool watch --config agents.yml
```

This command:

- Loads all agents from the configuration
- Sets up their event triggers (file watchers, webhooks, etc.)
- Keeps them running until interrupted (Ctrl+C)

### Configuration Example

```yaml
agents:
  file_processor:
    triggers:
      - type: file
        name: watch_docs
        paths: ["docs/**/*.md"]
        extensions: [".md"]
        enabled: true

  api_handler:
    triggers:
      - type: webhook
        name: api_endpoint
        port: 8000
        path: "/webhook"
        enabled: true
```

### Programmatic Usage

```python
async with AgentPool(config) as pool:
    # Events are automatically set up for configured triggers
    try:
        # Keep running until interrupted
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        # Clean shutdown when interrupted
        pass
```

Events are processed asynchronously, allowing agents to handle multiple triggers concurrently while maintaining their regular capabilities.
