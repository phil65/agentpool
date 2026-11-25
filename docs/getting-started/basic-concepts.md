---
title: Basic Concepts
description: Core concepts and architecture overview of AgentPool
icon: material/book
---

# Basic Concepts

## Node Hierarchy

AgentPool uses a unified **node** abstraction for all message-processing entities. This enables seamless composition and communication between different types of agents.


````python exec="true"
from agentpool.messaging import MessageNode
node = mk.MkClassDiagram(MessageNode, mode="subclasses")
print(node)
````




### MessageNode

The base interface that all nodes implement. A node receives prompts or messages, processes
them and emits responses, so agents and teams can be handled uniformly: run, streamed,
connected, used as tools, served over a protocol and logged to storage.

### Agents

All agent types are nodes, whatever runs them:

/// mknodes
{{ "agentpool.models.manifest.AnyAgentConfig" | config_union_table }}
///

```yaml
agents:
  assistant:                 # native PydanticAI agent (type: native is the default)
    model: openai:gpt-5-mini
    system_prompt: You are a helpful assistant.
  claude:
    type: claude_code        # Claude Code via its SDK
  goose:
    type: acp                # an ACP agent from the ACP registry
    provider: registry
    registry_id: goose
```

Native agents support every framework feature (tools, structured output, history
processing, ...). The others bring their own tools and loop; AgentPool adds its tools to
them over an MCP bridge and translates their events into its own.

### Teams

Groups of nodes that execute together:

- **Parallel teams** (`agent1 & agent2`, `mode: parallel`): all nodes run simultaneously
- **Sequential pipelines** (`agent1 | agent2`, `mode: sequential`): output flows from one to the next

Teams themselves are nodes, enabling nested composition.

## The Pool

An `AgentPool` loads a manifest (YAML) and owns everything defined in it: agents, teams, MCP
servers, storage, sessions, triggers and connections. Agents in a pool can discover each other
and delegate (`subagent` / `workers` toolsets), and the pool is what the protocol servers
(`serve-acp`, `serve-opencode`, `serve-mcp`, ...) expose to clients.

## Sessions and Turns

A **session** is one conversation with an agent; one pool agent can serve many sessions at
once, and delegation runs in child sessions. A **turn** is one prompt run in a session, until
it ends (completed, cancelled, failed, ...). See [Sessions and Turns](../core-concepts/sessions.md).

## Configuration in YAML

AgentPool is built around static, versionable YAML configuration validated by Pydantic
models (with a JSON schema for IDE completion):

```yaml
# agents.yml (AgentsManifest)
agents:
  web_assistant:
    model: openai:gpt-5
    tools:
      - type: import
        name: open_url
        import_path: webbrowser.open
    system_prompt: "You are a helpful web assistant."
```

In YAML you can:

- define agents of every type and teams
- assign tools, toolsets and MCP servers
- connect nodes (forward results, with conditions, queues and filters)
- define response types for structured output
- set up triggers (files, webhooks, schedules, MCP events, ...)
- configure storage providers for conversations, tool calls and commands
- load earlier conversations with simple queries

See [Configuration](../configuration/index.md) for the complete reference.

## Connections and Triggers

**Connections** forward a node's results to other nodes, files or callables (one-to-one or
one-to-many, queued, filtered, conditional). **Triggers** start a node on outside events: file
changes, webhooks, schedules, emails, MCP events or events of connections.
