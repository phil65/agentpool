---
title: Creating Agents
description: Guide to creating and configuring agents
icon: material/tools
---

# Creating Agents

There are several ways to create and initialize agents. Since agents can have complex setup requirements (MCP servers, runtime configuration, etc.), proper initialization is important.

## Main Approach: AgentPool

The recommended way to create agents is through `AgentPool`:

```python
async with AgentPool("agents.yml") as pool:
    # Get agent
    agent = pool.get_agent("analyzer")

    # Get agent with dependencies
    agent = pool.get_agent("reviewer", deps=pr_context)

    # Get structured agent
    agent = pool.get_agent("validator", output_type=ValidationResult)
```

This ensures:

- Proper async initialization of all components
- MCP server setup
- Runtime configuration
- Agent interconnections
- Resource loading

## Direct Agent Creation

For simpler cases, agents can be created directly:

```python
# Manual instantiation (requires more setup)
agent = Agent("agent_name", model="my_model")
async with agent:
    result = await agent.run("Hello!")
```

## Advanced Pool Creation

`AgentPool` offers additional creation methods:

```python
# Create from manifest
manifest = AgentsManifest.from_file("agents.yml")
pool = AgentPool(manifest)

# Create with manual configuration
pool = AgentPool(manifest, connect_nodes=False)

# Create with custom input provider
pool = AgentPool(manifest, input_provider=my_input_provider)
```

## Importance of Async Initialization

Agents require proper async initialization for:

1. MCP server setup and tool registration
2. Runtime configuration loading
3. Resource initialization
4. Connection setup

Always use async context managers:

```python
# ❌ Limited: Works, but not everything is initialized
agent = Agent(...)
result = await agent.run("Hello")

# ✅ Correct - proper async initialization
async with Agent(...) as agent:
    result = await agent.run("Hello")
```
