---
title: Agent Usage
description: Using agents in your application
icon: material/robot
---

# Agent

AgentPool provides multiple agent implementations with a shared interface: the native
PydanticAI-based `Agent`, `ClaudeCodeAgent`, `CodexAgent`, `ACPAgent`, `AGUIAgent` and more.
Most of the time they are defined in YAML and taken from a pool; the native agent can also be
created directly.

## Basic Usage

```python
from pydantic import BaseModel

from agentpool import Agent


class Analysis(BaseModel):
    summary: str
    suggestions: list[str]


async def main():
    # Agent with string output
    async with Agent(
        model="openai:gpt-5-mini",
        system_prompt="You are a helpful assistant.",
        tools=["webbrowser.open"],
    ) as basic_agent:
        await basic_agent.run("Open google for me.")  # Uses the tool to open a browser

    # Agent with structured output
    async with Agent(
        model="openai:gpt-5-mini",
        output_type=Analysis,
        system_prompt=[
            "You are a code analysis assistant.",
            "Always provide structured results.",
        ],
    ) as typed_agent:
        result = await typed_agent.run("Analyze this code.")
        print(result.content.summary)      # Typed access
        print(result.content.suggestions)  # Type-checked
```

## Agents from a Pool

```python
from agentpool import AgentPool


async def main():
    async with AgentPool("agents.yml") as pool:
        agent = pool.get_agent("assistant")
        result = await agent.run("Analyze this code.")
```

## Agent Configuration

```python
agent = Agent(
    name="code-assistant",          # Agent name
    model="openai:gpt-5-mini",      # Model to use
    output_type=Analysis,           # Optional result type
    system_prompt=[                 # Static system prompts
        "You are an assistant.",
        "Be concise and clear.",
    ],
    tools=[my_function],            # Callables or import paths
    mcp_servers=["uvx mcp-server-git"],
    retries=3,                      # Max retries
)
```

## Running the Agent

```python
# Basic run
result = await agent.run("Analyze this code.")

# Stream events
async for event in agent.run_stream("Analyze this."):
    print(event)

# Run a turn and inspect its outcome, usage and cost
turn = await agent.run_turn("Quick question")
```

See [Agents](../core-concepts/basic-agent.md) for the full interface.
