---
title: Knowledge System
description: Knowledge management and access
icon: material/book
---

## Overview

Knowledge gives an agent context that is loaded when it starts: files and URLs (any location
UPath / fsspec can read, glob patterns allowed) and prompts whose content is added to the
conversation. Content that tools read on demand is configured as
[resources](../configuration/resources.md) instead.

```yaml
agents:
  researcher:
    model: openai:gpt-5-mini
    knowledge:
      paths:
        - "docs/**/*.md"
        - "https://example.com/api-docs.md"
      prompts:
        - type: text
          description: Guidelines
          messages:
            - role: system
              content: "Consider these guidelines..."
      convert_to_markdown: true
```

All options: [Knowledge configuration](../configuration/knowledge.md).

## Initialization

Knowledge sources are loaded when the agent enters its async context:

```python
from agentpool import Agent
from agentpool_config.knowledge import Knowledge


async def main():
    knowledge = Knowledge(paths=["docs/**/*.md"])
    async with Agent(model="openai:gpt-5-mini", knowledge=knowledge) as agent:
        await agent.run("Use the documentation...")
```

Knowledge is loaded in parallel with other async setup (MCP servers, runtime) unless
`parallel_init=False` is set.
