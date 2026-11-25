---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool_config/storage.py
title: Storage 
description: Database and storage setup
icon: material/database
---

The storage configuration defines how agent interactions, messages, and tool usage are logged. It's defined at the root level of the manifest.

## Providers

/// mknodes
{{ "agentpool_config.storage.StorageProviderConfig" | config_union_table }}
///

Besides AgentPool's own SQL and file storage, providers read and write the native session
stores of Claude Code, Codex, Zed, T3 Code and ACP agents, so their conversations show up as
AgentPool sessions.

## Configuration Reference

/// mknodes
{{ "agentpool_config.storage.StorageConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="storage", header_level=3) }}
///

/// mknodes
{{ "agentpool_config.storage.StorageProviderConfig" | union_to_markdown(display_mode="yaml", header_style="pymdownx", header_level=3, wrapped_in="providers") }}
///

## Overview

Storage providers define how agent interactions, messages, and tool usage are persisted. The system supports multiple providers, from SQL databases and structured files to the session stores of other agents.

Key features:

- **Multiple Providers**: Use multiple storage backends simultaneously
- **Agent Filtering**: Control which agents are logged per provider
- **Flexible Logging**: Configure what gets logged (messages, conversations, commands, context)
- **Provider Selection**: Automatic or explicit provider selection for queries

## Usage Example

```yaml title="agents.yml"
--8<-- "docs/configuration/storage_example.yml"
```

## Configuration Notes

- Multiple providers can be used simultaneously
- Agent filtering works at both global and provider levels
- Provider flags are combined with global flags using AND logic
- SQL provider is recommended for production use
- Memory provider is useful for testing
- File storage writes YAML, JSON, TOML, INI or TOON (format from the file extension)
