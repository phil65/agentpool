---
title: Code Toolset
description: Code analysis and manipulation
icon: material/code-braces
---

# Code Toolset

Tools for code analysis and manipulation.

## Basic Usage

```yaml
agents:
  coder:
    model: openai:gpt-5-mini
    tools:
      - type: code
```

## Available Tools

```python exec="true"
from agentpool_toolsets.builtin.code import CodeTools
from agentpool.docs.utils import generate_tool_docs

toolset = CodeTools()
print(generate_tool_docs(toolset))
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.CodeToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///
