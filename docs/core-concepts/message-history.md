---
title: Message history
description: Message history and management
icon: material/history
---

## Overview

Each [session](sessions.md) keeps its conversation in a `MessageHistory`. For the agent's
default session it is available as `agent.conversation`. It provides:

- the messages of the conversation (`ChatMessage` objects)
- context messages added outside of runs
- loading earlier conversations from [storage](storage.md)
- temporary history changes (`temporary_state()`)

## Managing History

```python
history = agent.conversation

messages = history.get_history()     # all messages
history.set_history(messages[-10:])  # replace the history
await history.clear()                # start over

# Run with a temporary history, restored afterwards
async with history.temporary_state(["Earlier context"], replace_history=True):
    await agent.run("A question without the rest of the conversation")
```

## Context

Context is added to the conversation without running the agent:

```python
history.add_context_message(
    "Important background info",
    source="documentation",
    type="background",  # further keyword arguments are stored as metadata
)
await history.load_context_source("docs/api.md")  # a file or URL

# Format the history as text, for example for another agent
text = await history.format_history(max_tokens=1000, num_messages=5)
```

## Loading Earlier Conversations

The `session` field of a native agent loads a stored conversation when the agent starts,
by name or by a query (see [Session configuration](../configuration/session.md)):

```yaml
agents:
  my_agent:
    model: openai:gpt-5
    session:
      name: support_chat       # Session identifier
      since: 1h                # Time period to load
      until: 5m                # Up to this time ago
      agents: [support]        # Only messages of these agents
      roles: [user, assistant] # Only these roles
      contains: "error"        # Text search
      limit: 50                # Max messages
```

The same works in Python:

```python
from agentpool import Agent
from agentpool_config.session import SessionQuery


async def main():
    async with Agent(
        model="openai:gpt-5-mini",
        session=SessionQuery(name="support_chat", since="1h"),
    ) as agent:
        ...
```

The stored history of an agent is available with `await agent.get_message_history(limit=50)`.

## Compaction

Long conversations can be compacted: `await agent.compact()` shrinks the history of a session
(filtering, truncating or summarizing messages). The steps are configured in the manifest's
`compaction` section:

```yaml
compaction:
  preset: balanced  # or: minimal, summarizing

agents:
  assistant:
    model: openai:gpt-5-mini
```

/// mknodes
{{ "agentpool_config.compaction.CompactionConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="compaction", header_level=3) }}
///

/// mknodes
{{ "agentpool_config.compaction.CompactionStepConfig" | union_to_markdown(display_mode="yaml", header_style="pymdownx", header_level=3, wrapped_in="steps") }}
///
