---
title: Events & Triggers
description: Event system and handlers
icon: material/broadcast
---

Events allow agents to respond to external changes and automate actions based on various triggers.
AgentPool supports different types of triggers that can activate agents automatically.

- File system changes
- Webhook calls
- Incoming emails
- Time-based triggers
- Events of connections between agents (a message forwarded, a queue filled, ...)

## Basic Concepts

Events flow through the system in this order:

1. An event source detects a change (a trigger in an agent's or team's `triggers:`)
2. The event becomes a prompt (its `to_prompt()`), and the node the trigger belongs to runs it
3. Optional: the node's response flows on through its connections

## Event Types

### File Watch Events

Monitor file system changes and trigger agent actions:ä

```yaml
agents:
  code_monitor:
    triggers:
      - type: "file"
        name: "python_watcher"        # Unique identifier
        enabled: true                 # Can be disabled without removal
        paths: ["src/**/*.py"]        # Glob patterns to watch
        extensions: [".py"]           # Optional file type filter
        ignore_paths:                 # Optional ignore patterns
          - "**/__pycache__"
          - "**/.git"
        recursive: true               # Watch subdirectories
        debounce: 1600               # Minimum ms between triggers
```

### Webhook Events

Listen for HTTP requests:

```yaml
agents:
  api_handler:
    triggers:
      - type: "webhook"
        name: "github_webhook"
        enabled: true
        port: 8000
        path: "/github"
        secret: "${WEBHOOK_SECRET}"   # Optional validation secret
```

## Event Configuration

### Common Properties

All event types share these base properties:

```yaml
triggers:
  - name: "my_trigger"               # Unique identifier
    enabled: true                    # Whether trigger is active
    knowledge:                       # Optional knowledge to load
      paths: ["context/*.md"]        # Files to load as context
      resources:                     # AgentPool resources
        - type: "cli"
          command: "git status"
      prompts:                       # Context prompts
        - "Consider this background information..."
```

### Multiple Triggers

Agents can have multiple triggers of different types:

```yaml
agents:
  project_assistant:
    triggers:
      # Watch for code changes
      - type: "file"
        name: "code_watcher"
        paths: ["src/**/*.py"]
        extensions: [".py"]

      # Listen for GitHub webhooks
      - type: "webhook"
        name: "github_events"
        port: 8000
        path: "/github"

      # Manual review trigger
      - type: "manual"
        name: "review_code"
        prompt: "Review latest changes"
```

## Event Handling

### In Configuration

Configure how agents handle events through system prompts:

```yaml
agents:
  file_monitor:
    system_prompt: |
        You monitor file changes and analyze their impact.
        When receiving file change events:
        1. Check file type and content
        2. Assess impact of changes
        3. Recommend actions if needed
    triggers:
      - type: "file"
        name: "config_watch"
        paths: ["config/*.yml"]
```

## Where Triggers Run

A pool runs the triggers of all its agents and teams: their sources start once the nodes are
up, and stop before they shut down. An agent used without a pool runs its own triggers while
it is entered (`async with agent:`).

### Connection Triggers

A `connection` trigger has no source of its own. It fires on events of the pool's connections,
and runs its node in the background (the routing that caused the event doesn't wait for it):

```yaml
agents:
  writer:
    model: openai:gpt-5-nano
    connections:
      - type: node
        name: reviewer
  reviewer:
    model: openai:gpt-5-nano
  auditor:
    model: openai:gpt-5-nano
    triggers:
      - type: connection
        name: on_forward
        source: writer           # only connections from this node (optional)
        target: reviewer         # only connections to this node (optional)
        event: message_forwarded # message_received, message_processed, queue_filled, queue_triggered
        condition:               # optional
          type: content
          words: ["urgent"]
```

### Callbacks for One Connection

In Python, a connection (`Talk`) calls back on its own events:

```python
talk = writer.connect_to(reviewer)

def on_forward(event: ConnectionEventData) -> None:
    print(f"{event.connection_name}: {event.message.content}")

talk.on_event("message_forwarded", on_forward)
```

## CLI Usage

Run agents in event-watching mode to handle events:

```bash
# Start watching with default configuration
agentpool watch agents.yml

# With specific log level
agentpool watch agents.yml --log-level DEBUG
```

The agents will:

1. Start monitoring configured event sources
2. Handle events based on their configuration
3. Run until interrupted (Ctrl+C)

## Event Data

Each event type provides specific data:

### FileEvent

```python
@dataclass(frozen=True)
class FileEventData(EventData):
    path: str           # Path to affected file
    type: ChangeType    # "added" | "modified" | "deleted"
    timestamp: datetime # When event occurred
    source: str        # Trigger name
```

### WebhookEvent

```python
@dataclass(frozen=True)
class WebhookEventData(EventData):
    path: str          # Request path
    method: str        # HTTP method
    data: dict        # Request data
    timestamp: datetime
    source: str
```

## Common Patterns

### Code Review Automation

```yaml
agents:
  code_reviewer:
    triggers:
      - type: "file"
        name: "pr_watch"
        paths: ["src/**/*.py"]
    system_prompt: "You review Python code changes and provide feedback."
```

### Configuration Monitor

```yaml
agents:
  config_monitor:
    triggers:
      - type: "file"
        name: "config_watch"
        paths: ["config/*.yml"]
        debounce: 5000  # 5 second delay
```
