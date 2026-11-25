---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool_config/session.py
title: Sessions
description: Session management and configuration
icon: material/history
---

The `session` field of a native agent loads an earlier conversation from
[storage](storage.md) into the agent's history when it starts, and configures history
tracking. It takes a session name, a query (`SessionQuery`) or a memory configuration
(`MemoryConfig`) that contains a query.

!!! note
    This is about *loading history* from storage. Live sessions of a running pool (one per
    connected client conversation) are managed by `pool.sessions`; see
    [Sessions](../core-concepts/sessions.md).

## Basic Configuration

Simple session by name:

```yaml
agents:
  assistant:
    model: openai:gpt-5-mini
    session: "previous_chat"  # Simple session ID
```

You can also configure memory settings:

```yaml
agents:
  assistant:
    model: openai:gpt-5-mini
    session:
      enable: true            # Enable history tracking
      provider: "sql"         # Override default storage provider
      session:                # Previous session query
        name: "previous_chat"
```

## Detailed Query Configuration

Complex filtering of previous conversations:

```yaml
agents:
  assistant:
    model: openai:gpt-5-mini
    session:
      name: "coding_session"           # Session identifier
      agents: ["analyzer", "planner"]  # Filter by agent names
      since: "2h"                      # Time periods: 1h, 2d, 1w, etc.
      until: "1h"                      # Up until time period
      contains: "python"               # Filter by content
      roles: ["user", "assistant"]     # Filter by message roles
      limit: 100                       # Max messages to load
```

## Time Period Examples

The `since` and `until` fields support various formats:

```yaml
agents:
  assistant:
    model: openai:gpt-5-mini
    session:
      since: "2d"    # also: "30m", "1h", "1.5h", "1w"
      until: "1h"
```

## Role Filtering

Filter messages by their roles (`user`, `assistant`):

```yaml
agents:
  assistant:
    model: openai:gpt-5-mini
    session:
      roles:
        - "user"       # User messages
        - "assistant"  # Assistant responses
```

## Usage Examples

### Continue Previous Chat

```yaml
agents:
  assistant:
    model: openai:gpt-5-mini
    session: "last_chat"  # Simple continuation
```

### Load Recent History

```yaml
agents:
  assistant:
    model: openai:gpt-5-mini
    session:
      since: "24h"    # Last 24 hours
      limit: 50       # Max 50 messages
```

### Load Specific Topic

```yaml
agents:
  assistant:
    model: openai:gpt-5-mini
    session:
      contains: "project X"  # Messages about Project X
      agents: ["planner"]    # From planner agent
      since: "1w"           # From last week
```

### Team History

```yaml
agents:
  coordinator:
    model: openai:gpt-5-mini
    session:
      agents: ["researcher", "writer", "reviewer"]
      roles: ["assistant"]  # Only assistant messages
      since: "1d"          # From last day
```

## History Processors

History processors allow you to transform message history before each model call using custom Python functions. This is useful for filtering, summarizing, or modifying context dynamically.

### Basic Usage

```yaml
agents:
  my_agent:
    model: openai:gpt-5-mini
    session:
      history_processors:
        - my_module.filter_old_messages
        - my_module.summarize_history
```

Each processor is an import path to a callable with one of these signatures:

```python
# Simple synchronous processor
def processor(messages: list[ModelMessage]) -> list[ModelMessage]:
    return messages[-10:]  # Keep only last 10 messages

# Async processor
async def processor(messages: list[ModelMessage]) -> list[ModelMessage]:
    return await async_filter(messages)

# With RunContext access (for token usage, deps, etc.)
def processor(
    ctx: RunContext,
    messages: list[ModelMessage]
) -> list[ModelMessage]:
    if ctx.usage.total_tokens > 8000:
        return messages[-5:]
    return messages
```

### Execution Order

When both compaction pipeline and history processors are configured:

1. **CompactionPipeline** runs first (filtering, truncation)
2. **History Processors** run second (custom transformations)
3. **LLM Request** is made with processed history

### Security Considerations

History processors execute arbitrary Python code. Only use trusted import paths and avoid side effects (network calls, file I/O) in processors.

## Configuration Reference

/// mknodes
{{ "agentpool_config.session.SessionQuery" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="session", header_level=3) }}
///

/// mknodes
{{ "agentpool_config.session.MemoryConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="session", header_level=3) }}
///
