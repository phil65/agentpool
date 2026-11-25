---
title: Execution Environment Toolset
description: Execute code and commands
icon: material/console
---

# Execution Environment Toolset

The Execution Environment toolset provides tools for executing code and shell commands within a configured environment.

## Basic Usage

```yaml
agents:
  coder:
    model: openai:gpt-5-mini
    tools:
      - type: process_management
        environment:
          type: local
```

## Environment Types

The toolset supports various execution environments:

### Local

Execute on the local machine:

```yaml
tools:
  - type: process_management
    environment:
      type: local
      cwd: /workspace
```

### Docker

Execute in a Docker container:

```yaml
tools:
  - type: process_management
    environment:
      type: docker
      image: python:3.12
      volumes:
        /workspace: /app
```

### Remote

Execute on remote machines via SSH or other protocols.

## Available Tools

```python exec="true"
from agentpool_toolsets.builtin.execution_environment import ProcessManagementTools
from agentpool.docs.utils import generate_tool_docs

toolset = ProcessManagementTools()
print(generate_tool_docs(toolset))
```

## Tool Selection

You can limit which tools are exposed:

```yaml
tools:
  - type: process_management
    environment:
      type: local
    tools:
      - execute_command
      - execute_code
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.ProcessManagementToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///

## Security Considerations

- Use Docker or sandboxed environments for untrusted code
- Limit available tools to only what's needed
- Set appropriate working directories and permissions
