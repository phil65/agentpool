---
title: Agents
description: Agent types and the interface they share
icon: material/account
---

## Agent Types

Every agent in a pool implements the same interface (`BaseAgent`), whatever runs it:

/// mknodes
{{ "agentpool.models.manifest.AnyAgentConfig" | config_union_table }}
///

So a Claude Code agent, a Codex agent and a native PydanticAI agent can be run, streamed,
steered, delegated to, combined into teams and served over any protocol the same way. See
[Node Types](../configuration/node-types/index.md) for their configuration.

## Running an Agent

```python
from agentpool import AgentPool


async def main():
    async with AgentPool("agents.yml") as pool:
        agent = pool.get_agent("assistant")

        # Run and get the final message
        message = await agent.run("What is 2+2?")
        print(message.content)

        # Stream events (text deltas, tool calls, ...)
        async for event in agent.run_stream("Count to 10"):
            print(event)

        # Run a turn and inspect how it ended
        turn = await agent.run_turn("Summarize the README")
        print(turn.outcome, turn.usage)
```

A native agent can also be created directly:

```python
from agentpool import Agent


async def main():
    async with Agent(model="openai:gpt-5-mini", system_prompt="Be brief.") as agent:
        message = await agent.run("Hello!")
```

Runs belong to a [session](sessions.md): pass `session=` to run in a specific conversation;
otherwise the agent's default session is used.

## Structured Output

Native agents return typed results when given an `output_type`:

```python
from pydantic import BaseModel

from agentpool import Agent


class AnalysisResult(BaseModel):
    sentiment: float
    topics: list[str]


async def main():
    async with Agent(model="openai:gpt-5-mini", output_type=AnalysisResult) as agent:
        message = await agent.run("Analyze: 'I love this product!'")
        result: AnalysisResult = message.content
```

In YAML, `output_type` references a [response definition](../configuration/responses.md).

## Runtime Control

| Method | Purpose |
|---|---|
| `set_model(model)` / `get_available_models()` | Switch the model, list the models the agent can use |
| `get_modes()` / `set_mode(mode, category_id=...)` | Modes and config options (permission mode, reasoning effort, ...) |
| `steer(text)` / `queue_prompt(prompt)` / `interrupt()` | Talk to or stop a running turn |
| `compact()` / `fork_session()` / `start_new_session()` | Manage the conversation |
| `get_stats()` / `get_message_history()` | Usage statistics and stored history |
| `to_tool()` | Use the agent as a tool of another agent |
| `run_in_background(prompt, interval=..., max_count=...)` | Run repeatedly in the background |

## Signals

Agents emit signals that can be connected to:

```python
agent.message_sent.connect(lambda message: print("sent", message.content))
agent.message_received.connect(lambda message: print("received", message.content))
agent.turn_completed.connect(lambda turn: print("turn ended", turn.outcome))
agent.run_failed.connect(lambda event: print("failed", event))
```
