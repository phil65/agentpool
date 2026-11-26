---
title: Agent State
description: Agent state management
icon: material/database
---

# Managing Agent State in AgentPool

An agent maintains several types of state that influence its behavior. While some state is configured during initialization through YAML or constructor arguments, it is also possible to inspect and modify it during runtime.

## Core State Components

- **System Prompts**: Define the agent's role and behavior guidelines
- **Tools**: Available functions and capabilities (`agent.tools`, see [Tools](tools.md))
- **Memory/History**: Past conversation context
- **Model**: The used LLM model
- **Connections**: Active connections, to whom the own results get forwarded to.

## Runtime State Management

```python
async with Agent(...) as agent:
    # Change model
    await agent.set_model("openai:gpt-6-luna")
    # Clear conversation history
    await agent.conversation.clear()
    # Add context messages
    agent.conversation.add_context_message(
        "Important background information",
        source="knowledge_base"
    )
```

## Temporary State Changes

AgentPool's context managers allow temporary state modifications that automatically restore the original state:

```python
async with agent.temporary_state(
    tools=[callable_1, callable_2],  # Temporary tools
    replace_tools=True,  # Replace existing tools
    history=["Previous relevant chat"],  # Temporary history
    replace_history=True,  # Replace existing history
    pause_routing=True,  # Pause message routing
    model="openai:gpt-5",  # Temporary model
) as modified_agent:
    result = await modified_agent.run("Summarize this.")
    # Original state is restored after the block
```

!!! note
    System prompts cannot be changed at runtime. They are fixed when the agent enters its async context (`__aenter__`). This ensures prompt caching works correctly and prevents confusing mid-conversation changes.

## Memory Management

Control history tracking and loading of earlier conversations:

```python
from agentpool_config.session import MemoryConfig, SessionQuery

# No history tracking
agent = Agent(model="openai:gpt-5-mini", session=False)
# Memory configuration: history tracking, storage provider, history processors
agent = Agent(
    model="openai:gpt-5-mini",
    session=MemoryConfig(enable=True, history_processors=["my_module.keep_last_ten"]),
)

# Recover previous session
agent = Agent(model="openai:gpt-5-mini", session="previous_chat_id")
agent = Agent(
    model="openai:gpt-5-mini",
    session=SessionQuery(
        name="previous_chat",
        since="1h",  # Last hour's messages
        roles={"user", "assistant"},
    ),
)
```


All these mechanisms ensure that agent state can be precisely controlled while maintaining clean separation between temporary and permanent changes. The context managers are particularly useful for running agents with modified behavior without affecting their base configuration.
