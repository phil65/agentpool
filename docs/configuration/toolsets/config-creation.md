---
title: Config Creation Toolset
description: Create agent configurations
icon: material/cog-play
---

# Config Creation Toolset

Tools for writing configuration files (for example agent manifests) and validating them
against a JSON schema, so an agent can create configurations that are known to be valid.

## Basic Usage

```yaml
agents:
  admin:
    model: openai:gpt-5-mini
    tools:
      - type: config_creation
        schema_path: https://raw.githubusercontent.com/phil65/agentpool/main/schema/config-schema.json
        markup: yaml
```

## Available Tools

```python exec="true"
from agentpool_config.toolsets import ConfigCreationToolsetConfig
from agentpool.docs.utils import generate_tool_docs

config = ConfigCreationToolsetConfig(schema_path="schema/config-schema.json")
print(generate_tool_docs(config.get_provider()))
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.ConfigCreationToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///
