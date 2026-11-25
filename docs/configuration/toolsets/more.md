---
title: More Toolsets
description: Plan, debug, MCP discovery, cron and aggregating toolsets
icon: material/toolbox-outline
---

# More Toolsets

Smaller toolsets without a page of their own.

```yaml
agents:
  assistant:
    model: openai:gpt-5-mini
    tools:
      - type: plan  # keep a plan / todo list
      - type: debug  # inspect the agent, the pool and the logs at runtime
      - type: mcp_discovery  # find and use MCP servers from the registry on demand
        blocked_servers: ["some-server"]
      - type: cron  # schedule jobs at runtime (extra: bot)
      - type: aggregating  # several toolsets as one, optionally in code mode
        tool_mode: codemode
        toolsets:
          - type: file_access
          - type: process_management
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.PlanToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///

/// mknodes
{{ "agentpool_config.toolsets.DebugToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///

/// mknodes
{{ "agentpool_config.toolsets.MCPDiscoveryToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///

/// mknodes
{{ "agentpool_config.toolsets.CronToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///

/// mknodes
{{ "agentpool_config.toolsets.AggregatingToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///
