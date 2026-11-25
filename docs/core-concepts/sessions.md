---
title: Sessions and Turns
description: Conversations (sessions), the agents bound to them, and turns
icon: material/forum
---

## Sessions

A **session** is one conversation: its history, its working directory, the agent bound to it
and per-session overrides (model, mode, instructions). Sessions are separate from the agents
themselves, so one pool agent can serve many conversations at the same time, each with its
own history.

- Protocol servers (ACP, OpenCode V2, AG-UI, MCP, ...) open one session per client
  conversation.
- Delegation creates **child sessions**: a subagent task, a worker call or a team member run
  in a child session whose `parent_id` is the caller's session.
- Sessions are persisted by the [storage providers](storage.md) and can be resumed, forked
  (copying the history up to a message) and listed.

The live sessions of a pool are in `pool.sessions` (a `SessionRegistry`):

```python
from agentpool import AgentPool


async def main():
    async with AgentPool("agents.yml") as pool:
        agent = pool.get_agent("assistant")

        # Each session has its own conversation with the same agent
        first = pool.sessions.create("assistant")
        second = pool.sessions.create("assistant")
        await agent.run("My name is Ada.", session=first)
        await agent.run("My name is Grace.", session=second)
        answer = await agent.run("What is my name?", session=first)
        print(answer.content)  # Ada

        # Fork a conversation into a new child session
        branch = pool.sessions.fork(first.session_id)
        await agent.run("Actually, call me Countess.", session=branch)
```

Without `session=`, an agent runs in its **default session**; `agent.start_new_session()`
detaches from it so the next run starts a fresh conversation. Agents also offer
`fork_session()`, `compact()` (summarize / shrink the history), `list_sessions()` and
`load_session()` / `resume_session()` for stored sessions, including the native sessions of
external agents (Claude Code, Codex, ACP agents).

## Turns

A **turn** is one prompt run in a session of a node: the model requests, tool calls and
subagent runs it causes, until it ends. `agent.run_turn()` returns a `Turn`:

```python
turn = await agent.run_turn("Summarize the README")
print(turn.outcome)  # TurnCompleted, TurnCancelled, TurnLimitReached, TurnFailed or TurnRefused
print(turn.result)   # the final ChatMessage, if any
print(turn.usage, turn.cost, turn.duration)
for child in turn.children:  # turns of subagents started by this turn
    print(child.node, child.outcome)
```

`agent.run()` and `agent.run_stream()` run a turn as well and return the final message or
the stream of events.

## Talking to a Running Turn

While a turn runs, more input can reach it:

| Method | Effect |
|---|---|
| `agent.steer(text)` | Deliver user input into the running turn (at the next step boundary). |
| `agent.queue_prompt(prompt)` | Run the prompt as the next turn after the current one. |
| `agent.inject_prompt(text)` | Add context to the conversation mid-run (meant for tools). |
| `agent.interrupt()` | Cancel the running turn. |

Protocol clients use the same operations (for example ACP's `session/cancel` or OpenCode's
steering of a running session).
