---
title: Import Tools Toolset
description: Import individual functions as tools
icon: material/import
---

# Import Tools Toolset

Import arbitrary Python functions as agent tools via import paths.

## Basic Usage

```yaml
agents:
  my_agent:
    model: openai:gpt-5-mini
    tools:
      - type: import_tools
        tools:
          - import_path: os.listdir
            name: list_files
            description: "List directory contents"
          - import_path: webbrowser.open
            description: "Open URL in browser"
```

## Tool Configuration

Each tool supports these options:

| Field | Description |
|-------|-------------|
| `import_path` | Python import path (e.g., `os.listdir`, `mymodule:func`) |
| `name` | Override the tool name |
| `description` | Override the tool description |
| `enabled` | Whether tool is initially enabled (default: true) |
| `requires_confirmation` | Require user confirmation before execution |
| `metadata` | Additional metadata dict |

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.ImportToolsToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///

## Use Cases

- Quick addition of stdlib functions as tools
- Exposing existing library functions to agents
- Prototyping before creating a full toolset
