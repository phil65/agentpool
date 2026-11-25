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

## Security notes

- `run_diagnostics` runs external checkers (ty, pyright, mypy, biome, oxlint, ...) in the
  agent's execution environment. These programs read project configuration and may load
  plugins, so the tool is an `execute` tool that requires confirmation when the agent uses
  `requires_tool_confirmation: per_tool` (the default). Use `requires_tool_confirmation: never`
  only for trusted workspaces.
- File names are passed to the checkers as separately quoted arguments, so names containing
  spaces or shell metacharacters (`;`, `$(...)`, backticks) are never interpreted by a shell.

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.CodeToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///
