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

Monitor file system changes and trigger agent actions:

```yaml
agents:
  code_monitor:
    model: openai:gpt-5-mini
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
    model: openai:gpt-5-mini
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

All trigger types share these base properties:

```yaml
triggers:
  - type: time
    name: "my_trigger"               # Unique identifier
    enabled: true                    # Whether trigger is active
    session: target                  # Session policy (see below)
    template: "{{ content }}"        # Jinja2 template turning the event into a prompt
    include_metadata: true
    include_timestamp: true
    schedule: "0 9 * * 1-5"
    prompt: "Write the weekday report"
```

The full list of trigger types and their options is in
[Event Sources](../configuration/event-sources.md).

### Multiple Triggers

Agents can have multiple triggers of different types:

```yaml
agents:
  project_assistant:
    model: openai:gpt-5-mini
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

      # Daily review
      - type: "time"
        name: "daily_review"
        schedule: "0 18 * * *"
        prompt: "Review today's changes"
```

## Event Handling

### In Configuration

Configure how agents handle events through system prompts:

```yaml
agents:
  file_monitor:
    model: openai:gpt-5-mini
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

### Trigger Sessions

By default an event runs in the node's own session. A trigger can pick another:

```yaml
agents:
  monitor:
    model: openai:gpt-5-nano
    triggers:
      - type: time
        name: daily_report
        schedule: "0 9 * * *"
        prompt: "Write the daily report"
        session: per_event   # target (default) | standing | per_event
```

- `target`: the node's own session.
- `standing`: one session for the trigger, kept and resumed (`trigger/<node>/<trigger>`).
- `per_event`: a new session for every event.

Other than `target` only for agents: a team runs its members in sessions of its own.

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

### MCP Event Triggers

An `mcp_event` trigger subscribes to events an MCP server publishes through the MCP Events
extension ([SEP-3415](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3415),
experimental), and runs its node on each:

```yaml
agents:
  triager:
    model: openai:gpt-5-nano
    mcp_servers:
      - type: streamable-http
        name: incidents
        url: https://incidents.example.com/mcp
        events: true               # opt into the server's events
    triggers:
      - type: mcp_event
        name: new_incidents
        server: incidents          # one of the node's or the pool's MCP servers
        event: incident.triggered  # as the server lists it
        arguments: {service: checkout}
        prompt: "New incident: {{ event.data.title }}"
        session: per_event
```

- The server has to offer the event, and `arguments` must match its input schema: both are
  checked when the trigger starts.
- `delivery: auto` (default) uses push when the server offers it, else poll (`push`, `poll`).
- `resume: true` (default) keeps the position after each handled event (in the agentpool data
  directory), so a restart first delivers what arrived meanwhile. An event whose run a stop cut
  short is delivered again. With `resume: false` a subscription starts from now.
- Without `prompt`, the prompt is the event's name and data.

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

# With debug logging (a global option)
agentpool --log-level debug watch agents.yml
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
    model: openai:gpt-5-mini
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
    model: openai:gpt-5-mini
    triggers:
      - type: "file"
        name: "config_watch"
        paths: ["config/*.yml"]
        debounce: 5000  # 5 second delay
```
