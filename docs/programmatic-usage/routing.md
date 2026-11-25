---
title: Routing
description: Message routing strategies
icon: material/source-branch
---

# Message Routing System

## Overview

AgentPool's message routing system provides a flexible and powerful way to control how messages flow between agents. Unlike simple point-to-point connections, the routing system allows for sophisticated decision-making based on message content, agent state, and historical statistics.

## Basic Concepts

At its core, routing is handled through filter functions that determine whether a message should be forwarded to a specific target. These functions can consider:

- Message content and metadata
- Target agent's capabilities and state
- Historical interaction statistics
- System-wide metrics

## Filter Functions

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

### Routing Decisions

Filter functions can make routing decisions based on:

1. **Message Content and Metadata**
   - Content analysis
   - Message role
   - Cost information
   - Custom metadata

2. **Target Agent State**
   - Current workload
   - Capabilities
   - Model type
   - Connection status

3. **Connection Statistics**
   - Message count
   - Token usage
   - Historical interaction
   - Timing information

### Advanced Routing Patterns

#### Load Balancing

```python
def least_busy(context: EventContext):
    return not context.target.is_busy()

(source >> team).when(least_busy)
```

#### Cost-Aware Routing

```python
def within_budget(context: EventContext):
    return context.stats.total_cost < 1.0  # $1 limit

(source >> target).when(within_budget)
```

### Context Object

All conditions and triggers receive a rich context object providing access to the complete state:

```python
@dataclass(frozen=True)
class EventContext:
    """Context for condition checks and event handling."""

    message: ChatMessage[Any]
    """The message being processed."""

    target: MessageNode
    """The target node this message is being sent to."""

    stats: TalkStats
    """Statistics for the current connection."""

    registry: ConnectionRegistry | None
    """Registry of all named connections."""

    talk: Talk
    """The Talk instance handling this message flow."""
```

This context allows conditions to:

- Inspect the current message
- Access connection statistics
- Look up other connections by name
- Make decisions based on complete state

Example usage:

```python
def check_condition(ctx: EventContext) -> bool:
    # Check current message
    if "error" in ctx.message.content:
        return False

    # Check connection stats
    if ctx.stats.message_count > 10:
        return False

    # Look up other connection
    if ctx.registry and (other := ctx.registry.get("other_connection")):
        if other.stats.message_count > 5:
            return False

    return True

# Use in connection
connection = agent.connect_to(
    other_agent,
    filter_condition=check_condition
)
```

The context object provides a clean interface for accessing all relevant information in one place, making conditions both powerful and easy to write.

### Async Support

Filter functions can be either synchronous or asynchronous:

```python
# Sync filter
agent >> other.when(lambda msg: "urgent" in str(msg.content))

# Async filter
async def check_database(msg: ChatMessage, target: MessageNode):
    result = await db.check_priority(msg.content)
    return result == "high"

agent >> other.when(check_database)
```

### Combining Conditions

Multiple conditions can be combined using regular Python logic:

```python
def complex_route(msg: ChatMessage, target: MessageNode, stats: TalkStats):
    return (
        # Content-based
        any(topic in str(msg.content) for topic in ["urgent", "critical"]) and
        # Target-based
        target.capabilities.can_execute_code and
        not target.is_busy() and
        # Stats-based
        stats.message_count < 100 and
        stats.total_cost < 5.0
    )

source >> target.when(complex_route)
```

### Error Handling

Filter functions should handle their own exceptions. Any unhandled exceptions will prevent message routing:

```python
def safe_route(msg: ChatMessage, target: MessageNode):
    try:
        return complex_check(msg.content)
    except Exception as e:
        logger.warning("Routing check failed: %s", e)
        return False

source >> target.when(safe_route)
```

This routing system provides:

- Flexible decision-making
- Access to relevant context
- Type safety through signature checking
- Async support
- Clean, functional interface
