---
title: Example Configurations
description: Ready-to-use agent configuration examples
icon: material/file-code
---

# Examples

These examples demonstrate how to create and use agents through YAML configuration files.

## Simple Text Agent

Create a simple agent that opens websites in your browser:

```yaml title="agents.yml"
--8<-- "docs/getting-started/example_simple.yml"
```

Use the agent via an ACP client of your choice.

Or programmatically:

```python
from agentpool import AgentPool

async with AgentPool("agents.yml") as pool:
    agent = pool.get_agent("url_opener")
    result = await agent.run("Open the Python website")
    print(result.content)
```

## Structured Responses

Define structured outputs for consistent response formats:

```yaml title="agents.yml"
--8<-- "docs/getting-started/example_structured.yml"
```

## Tool Usage

Create an agent that interacts with the file system:

```yaml title="agents.yml"
--8<-- "docs/getting-started/example_tools.yml"
```

Use the file manager:

```python
from agentpool import AgentPool

async with AgentPool("agents.yml") as pool:
    agent = pool.get_agent("file_manager")
    # List files
    result = await agent.run("What files are in the current directory?")
    print(result.content)

    # Read a file
    result = await agent.run("Show me the contents of config.py")
    print(result.content)
```
