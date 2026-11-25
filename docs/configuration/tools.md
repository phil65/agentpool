---
title: Tools
description: Single tools for agents (bash, read, grep, question, import paths, ...)
icon: material/hammer-wrench
---

An agent's `tools` list takes single tools, [toolsets](toolsets/index.md) (collections of
tools) and [built-in tools](builtin-tools.md) of the model provider, all selected by `type`.
A plain string is an import path of a function to use as a tool.

```yaml
agents:
  assistant:
    model: openai:gpt-5-mini
    tools:
      - webbrowser.open  # import path shorthand
      - type: import
        import_path: os.listdir
        name: list_files
        description: List the files of a directory
      - type: bash
        requires_confirmation: true
      - type: read
      - type: grep
      - type: question  # ask the user
```

## Available Tools

/// mknodes
{{ "agentpool.models.agents.AnyToolConfig" | config_union_table }}
///

## Single Tool Reference

/// mknodes
{{ "agentpool_config.tools.ImportToolConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///

/// mknodes
{{ "agentpool_config.agentpool_tools.AgentpoolToolConfig" | union_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///
