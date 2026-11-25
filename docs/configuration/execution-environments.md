---
title: Execution environments
description: Where an agent's code runs and its processes and files live
icon: material/server
---

Execution environments decide where an agent's tools run code, execute commands, manage
processes and access files: locally, in a container, over SSH or in a cloud sandbox. They
come from [exxec](https://github.com/phil65/exxec).

Every agent has an `environment` field. A string is a working directory on the local
machine; a config selects another environment by `type`:

```yaml
agents:
  local:
    model: openai:gpt-5-mini
    environment: /path/to/project
    tools:
      - type: process_management

  sandboxed:
    model: openai:gpt-5-mini
    environment:
      type: docker
      image: python:3.13-slim
    tools:
      - type: execute_code
      - type: bash
```

ACP agents also have a `client_execution_environment` for the file and terminal requests of
the agent process (see [ACP Integration](../advanced/acp-integration.md)). When the pool is
served over ACP, a session's environment is the client's: file and terminal operations go to
the IDE.

## Available Environments

/// mknodes
{{ "exxec_config.ExecutionEnvironmentConfig" | config_union_table }}
///

## Configuration Reference

/// mknodes
{{ "exxec_config.ExecutionEnvironmentConfig" | union_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="environment", header_level=3) }}
///
