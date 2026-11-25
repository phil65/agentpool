---
title: Entry Points Toolset
description: Load tools from Python entry points
icon: material/import
---

# Entry Points Toolset

Load tools registered through Python entry points, enabling plugin-style tool discovery.

## Basic Usage

```yaml
agents:
  my_agent:
    model: openai:gpt-5-mini
    tools:
      - type: entry_points
        module: my_package
```

## How It Works

A package registers a `tools` entry point in the `agentpool` group of its `pyproject.toml`,
pointing to a function that returns the tool callables:

```toml
[project.entry-points.agentpool]
tools = "my_package.tools:get_tools"
```

The toolset then discovers and loads these tools automatically.

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.EntryPointToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools") }}
///
