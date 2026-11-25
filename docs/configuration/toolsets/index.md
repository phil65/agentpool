---
title: Toolsets
description: Toolset configuration overview
icon: material/toolbox
order: 1
---

# Toolsets

Toolsets are collections of tools that can be dynamically loaded and assigned to agents. They provide a modular way to extend agent capabilities.


## Overview

Toolsets are configured in the agent's `tools` list, next to [single tools](../tools.md):

```yaml
agents:
  my_agent:
    model: openai:gpt-5-mini
    tools:
      - type: file_access
        fs: "file:///workspace"
      - type: search
        web_search: tavily
```

## Available Toolsets

/// mknodes
{{ "agentpool_config.toolsets.ToolsetConfig" | config_union_table }}
///

## Common Configuration

All toolsets share these base options:

```yaml
tools:
  - type: <toolset_type>
    namespace: optional_prefix  # Prefix for tool names
```

The `namespace` field helps prevent name collisions when using multiple toolsets.
