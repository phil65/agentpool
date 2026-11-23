---
title: Talk & TeamTalk
description: Agent communication system
icon: material/comment-multiple
---

# Agent Connection System

## Overview

AgentPool provides a robust, object-oriented approach to managing agent communications through dedicated connection objects. The system supports various connection patterns and offers fine-grained control over message flow and monitoring.

## Core Components

### Talk

The fundamental connection unit representing a one-to-many relationship between agents:

```python
class Talk:
    def __init__(
        self,
        source: MessageNode[Any, Any],
        targets: list[MessageNode[Any, Any]],
        connection_type: ConnectionType = "run",
        priority: int = 0,
        delay: timedelta | None = None,
    )
```

### Connection Types

Three different ways messages can be handled:

- `run`: Execute message as a new run in target agent
- `context`: Add message as context to target's conversation
- `forward`: Forward message directly without processing

### Connection Management

Connections are managed by the `ConnectionManager`, which provides:

- Connection creation and cleanup
- Message routing
- Wait state management
- Statistics tracking

## Connection Patterns

### Agent-to-Agent

Simple connection between two agents:

```python
# Direct connection
agent_a.connect_to(agent_b)

# Named connection (using pool)
agent_a.connect_to("agent_b")
```

### Agent-to-Team

Connect an agent to multiple targets:

```python
# Create team
team = agent_b & agent_c & agent_d

# Connect agent to team
agent_a.connect_to(team)
```

### Team-to-Team

Connect groups of agents:

```python
team_a = agent_1 & agent_2
team_b = agent_3 & agent_4
team_a.connect_to(team_b)
```

## Message Flow Control

### Statistics and Monitoring

Each connection tracks:

- Message count
- Token usage
- Byte count
- Timing information

```python
@dataclass(frozen=True)
class TalkStats:
    message_count: int
    token_count: int
    byte_count: int
    last_message_time: datetime | None
    source_name: str | None
    target_names: set[str]
```

### Flow Control

Connections support:

- Priority-based message handling
- Delayed execution
- Message filtering
- State tracking (active/inactive)

```python
# Set up connection with control
talk = agent.connect_to(
    target,
    priority=1,
    delay=timedelta(seconds=5)
)

# Filter messages
talk.when(lambda msg: "important" in msg.content)
```

### Team Management

If a team is connected to other entities, a TeamTalk object is returned, containing multiple one-to-many connections.
The TeamTalk object provides a similar interface to the Talk object and forwards the method calls to all contained Talk objects.

`TeamTalk` provides aggregate operations for multiple connections:

- Collective statistics
- Group operations (pause/resume)
- Recursive target resolution

## Message Routing

### Basic Routing

The connection system supports flexible message routing through filter conditions. These can be defined using the `when` method, which accepts filter functions with varying levels of detail:

```python
# Simple message-based routing
agent >> other_agent.when(
    lambda msg: "urgent" in msg.content
)

# Target-aware routing
agent >> other_agent.when(
    lambda msg, target: (
        "code" in msg.content and
        not target.is_busy()
    )
)

# Full context routing (message, target, stats)
agent >> other_agent.when(
    lambda msg, target, stats: (
        "database" in msg.content and
        target.capabilities.can_execute_code and
        stats.message_count < 10
    )
)
```

### Manual Message Triggering

Connections can be configured for manual message processing using `queued=True`. This allows precise control over when messages flow through the connection:

```python
# Create queued connection
talk = agent_a.connect_to(
    agent_b,
    queued=True,
    queue_strategy="latest"  # Only process most recent message
)

# Manually trigger message processing
responses = await talk.trigger()
```

### Queue Strategies

When using queued connections, different strategies are available for processing pending messages:

- `latest`: Process only the most recent message (default)
- `concat`: Combine all pending messages with newlines
- `buffer`: Process all messages individually in order

```python
# Process all messages
talk = source.connect_to(
    target,
    queued=True,
    queue_strategy="buffer"
)

# Combine pending messages
talk = source.connect_to(
    target,
    queued=True,
    queue_strategy="concat"
)
```

### Connection States

Connections can be temporarily paused or permanently disconnected:

```python
# Temporarily pause
async with talk.paused():
    # Connection won't process messages
    await do_something()

# Permanently disconnect
talk.disconnect()
```

### Message Transformation

Messages can be transformed before being forwarded:

```python
talk = agent_a.connect_to(
    agent_b,
    transform=lambda msg: f"Processed: {msg.content}"
)

# Async transforms
async def add_metadata(msg):
    msg.metadata["processed"] = True
    return msg

talk = agent_a.connect_to(
    agent_b,
    transform=add_metadata
)
```

### Execution Control

Fine-grained control over message execution:

```python
talk = agent_a.connect_to(
    agent_b,
    priority=1,  # Lower numbers = higher priority
    delay=timedelta(seconds=5),  # Delay before processing
)
# With a delay agent_b runs in the background: make agent_a's runs wait for it
# (YAML: wait_for_completion)
agent_a.connections.set_wait_state(agent_b)
```

### Stop and Exit Conditions

Connections support conditions for stopping or exiting:

```python
# Stop this connection when condition met
talk = agent_a.connect_to(
    agent_b,
    stop_condition=lambda ctx: "stop" in ctx.message.content
)

# Exit the application when condition met
talk = agent_a.connect_to(
    agent_b,
    exit_condition=lambda msg: "emergency" in msg.content
)
```

### Monitoring and Signals

Connections emit signals for monitoring:

```python
# Listen for original messages
talk.message_received.connect(lambda msg: print(f"Received: {msg}"))

# Listen for transformed/processed messages
talk.message_forwarded.connect(lambda msg: print(f"Forwarded: {msg}"))
```

### Message Queuing

Queued connections store messages per target:

```python
talk = agent_a.connect_to(
    agent_b,
    queued=True
)

# Check pending messages
pending = talk._pending_messages[agent_b.name]

# Process with custom prompt
responses = await talk.trigger("Additional context")
```

## Unique Features

1. **Object-Oriented Design**
   - Each connection is a first-class object
   - Strong typing and clear interfaces
   - Easy to extend and customize

2. **Flexible Routing**
   - Multiple connection types
   - Configurable message handling
   - Support for complex topologies

3. **Rich Monitoring**
   - Detailed statistics
   - Connection state tracking
   - Performance metrics

4. **Type Safety**
   - Generic type parameters for dependencies and results
   - Type-safe message passing
   - Proper typing for team operations

5. **Clean API**
   - Operator overloading for intuitive syntax (`agent >> other_agent`)
   - Chainable configuration
   - Consistent interface across patterns

## Example Usage

```python
# Create agents
analyzer = Agent(name="analyzer")
planner = Agent(name="planner")
executor = Agent(name="executor")

# Create team
planning_team = planner & executor

# Set up connection
talk =analyzer.connect_to(planning_team, connection_type="run")

talk.when(lambda msg: msg.metadata.get("priority") == "high")

# Monitor
print(f"Processed {talk.stats.message_count} messages")
```
