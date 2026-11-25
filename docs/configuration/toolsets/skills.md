---
title: Skills Toolset
description: Load and execute skills
icon: material/lightning-bolt
---

# Skills Toolset

Load and execute skills - reusable prompt-based capabilities.

## Basic Usage

```yaml
agents:
  my_agent:
    model: openai:gpt-5-mini
    tools:
      - type: skills
```

## Available Tools

```python exec="true"
from agentpool_toolsets.builtin.skills import SkillsTools
from agentpool.docs.utils import generate_tool_docs

toolset = SkillsTools()
print(generate_tool_docs(toolset))
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.SkillsToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///
