---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool_config/forward_targets.py
title: Connections 
description: Message routing and forwarding configuration
icon: material/arrow-right
---

Connections define how messages are automatically forwarded from agents to other destinations. They enable agent pipelines, parallel processing, and message routing patterns.

## Overview

Connections allow you to:

- Forward messages between agents for sequential processing
- Save messages to files with custom formatting
- Process messages through custom Python functions
- Create agent pipelines and workflows
- Implement complex message routing patterns

AgentPool supports three connection types:

- **Node Connection**: Forward to another agent, team, or node
- **File Connection**: Save messages to files with templating
- **Callable Connection**: Process through Python functions

## Configuration Reference

/// mknodes
{{ "agentpool_config.forward_targets.ForwardingTarget" | union_to_markdown(display_mode="yaml", header_style="pymdownx") }}
///

## Connection Types

### Node Connection

Forward messages to other agents or teams:

```yaml
agents:
  analyzer:
    connections:
      - type: node
        name: "summarizer"
        connection_type: "run"
        wait_for_completion: true
```

Connection types:

- `run`: Execute target agent with the message
- `queue`: Add message to target's queue
- `forward`: Simple message forwarding

### File Connection

Save messages to files with Jinja2 templating:

```yaml
agents:
  logger:
    connections:
      - type: file
        path: "logs/{date}/{agent}.txt"
        template: "[{timestamp}] {name}: {content}"
        encoding: "utf-8"
```

### Callable Connection

Process messages through Python functions:

```yaml
agents:
  processor:
    connections:
      - type: callable
        callable: "myapp.handlers:process_message"
        kw_args:
          format: "json"
```

## Message Flow Control

### Delay

Run the delivery later, in the background:

```yaml
connections:
  - type: node
    name: "worker"
    delay: 5.0  # Deliver 5 seconds after the source's turn ends
```

Without a delay, a `run` connection runs the target as the source's turn ends, and the source's
run returns after it. A delivery with a delay runs in the background instead.

### Wait for Completion

Whether the source's run waits for its background deliveries (and what they route on):

```yaml
connections:
  - type: node
    name: "worker"
    delay: 5.0
    wait_for_completion: true  # The source's run returns once the worker is done
```

### Target Session

Which session of the target the messages run in (for `run` and `context` connections):

```yaml
connections:
  - type: node
    name: "reviewer"
    session: per_source_session
```

- `target` (default): the target's own session.
- `per_source_session`: one per session of the source, as a child of it
  (`<source session>/<connection>/<target>`). Each conversation of the source has its own
  conversation with the target.
- `standing`: one session for the connection, kept and resumed (`connection/<connection>/<target>`).
- `per_message`: a new session for every message.

Other than `target` only for agents: a team runs its members in sessions of its own.

## Conditions

Apply conditions to control when messages are forwarded:

```yaml
connections:
  - type: node
    name: "expensive_agent"
    filter_condition:
      type: word_match
      words: ["urgent", "important"]
    stop_condition:
      type: cost_limit
      max_cost: 1.0
```

See [Conditions Configuration](conditions.md) for details on available condition types.

## Queue Management

Control message queueing behavior:

```yaml
connections:
  - type: node
    name: "worker"
    connection_type: "queue"
    queue_max_size: 100
    queue_trigger_size: 10  # Process when queue reaches this size
```

## Configuration Notes

- Connections are evaluated in the order they are defined
- Multiple connections can be active simultaneously
- File paths support variable substitution (date, agent name, etc.)
- Callable connections can be sync or async functions
- Queue connections allow batching messages for efficiency
- Conditions provide fine-grained control over message flow
- Priority affects order of processing when multiple messages are queued
