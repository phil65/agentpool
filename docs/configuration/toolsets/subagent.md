---
title: Subagent Toolset
description: Delegate tasks to other agents
icon: material/account-group
---

# Subagent Toolset

The Subagent toolset enables agents to delegate tasks to other agents in the pool.

## Basic Usage

```yaml
agents:
  coordinator:
    model: openai:gpt-5-mini
    tools:
      - type: subagent
```

## Available Tools

```python exec="true"
from agentpool_toolsets.builtin.subagent_tools import SubagentTools
from agentpool.docs.utils import generate_tool_docs

toolset = SubagentTools()
print(generate_tool_docs(toolset))
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.SubagentToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///

## Example Workflow

```yaml
agents:
  coordinator:
    model: openai:gpt-5-mini
    tools:
      - type: subagent
    system_prompt: You coordinate work between specialized agents.
  
  researcher:
    model: openai:gpt-4o
    system_prompt: You research topics thoroughly.
  
  writer:
    model: openai:gpt-4o
    system_prompt: You write clear documentation.
```

The coordinator can then delegate research to the researcher and writing to the writer.
