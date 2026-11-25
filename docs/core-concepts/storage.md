---
title: Storage System
description: Storage and persistence system
icon: material/database
---

The storage system in AgentPool provides flexible logging and history management through multiple storage providers. It allows tracking conversations, tool usage, and commands while supporting different storage backends and filtering options.

## Overview

The storage system serves multiple purposes:

- Recording conversation history for context and analysis
- Logging tool usage and command execution
- Enabling conversation recovery and continuation
- Supporting debugging and monitoring
- Allowing to pretty-print and output conversations in a customizable way

Multiple providers can be active simultaneously, each handling different aspects of storage:

- Some providers focus on full history tracking
- Others specialize in output logging or monitoring
- Providers can be filtered to handle specific agents

Settings can be configured at two levels:

- Global settings affecting all providers
- Provider-specific settings for fine-grained control

## Storage Providers

### SQL Storage

The SQL provider offers full history tracking with database persistence:

```yaml
storage:
  providers:
    - type: sql
      url: sqlite:///history.db  # Default location
      pool_size: 5              # Connection pool size
      auto_migration: true      # Add missing columns automatically
```

Features:

- Complete conversation history
- Efficient querying and filtering
- Support for SQLite and other SQL databases
- Automatic schema management
- Default provider if none specified

### File Storage

File-based storage using YAML or JSON formats:

```yaml
storage:
  providers:
    - type: file
      path: history.yml        # Or .json
      format: auto            # Or yaml/json
      encoding: utf-8
```

Features:

- Human-readable storage format
- Easy version control integration
- Flexible file organization
- Support for multiple file formats


### Memory Storage

In-memory storage for testing and development:

```yaml
storage:
  providers:
    - type: memory
```

Provides temporary storage without persistence, ideal for testing and development.

## Provider Capabilities

History Providers (SQL, File):

- Full conversation history
- Message querying and filtering
- Session recovery
- Statistics and analytics


## Configuration

Global Settings:

```yaml
storage:
  # Global filters
  agents: [agent1, agent2]     # Only log these agents
  log_messages: true           # Enable message logging
  log_sessions: true      # Enable conversation tracking

  log_commands: true          # Enable command logging

  # Provider handling
  filter_mode: and            # How to combine filters (and/override)
  default_provider: sql       # Provider for history queries
```

Provider Filtering:

- Global agent filters
- Provider-specific filters
- Combination modes:
  - `and`: Both global and provider filters must match
  - `override`: Provider filter takes precedence

Multiple Providers Example:

```yaml
storage:
  providers:
    - type: sql               # Main history storage
      url: sqlite:///history.db

    - type: file              # Additional log file (format from the extension)
      path: logs/history.yml
```

## Usage Examples

Basic Setup:

```yaml
storage:
  providers:
    - type: sql              # Single provider
```

Multiple Providers:

```yaml
storage:
  providers:
    - type: sql             # Main storage
      url: postgresql://...
    - type: file           # Backup
      path: backup.yml
```

History Queries:

```python
# Get recent messages
messages = await storage.filter_messages(
    SessionQuery(
        name="session_1",
        since="1h",
        roles={"assistant"}
    )
)

# Get statistics
stats = await storage.get_session_stats(
    filters=StatsFilters(
        cutoff=datetime.now(UTC) - timedelta(days=1),
        group_by="agent"
    )
)
```

Custom Filtering:

```yaml
storage:
  # Global filter
  agents: [assistant, researcher]

  providers:
    - type: sql
    - type: opencode
