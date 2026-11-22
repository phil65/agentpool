---
title: Node Types
description: Agent and team node configuration
icon: material/family-tree
order: 0
---

# Node Types

Nodes are the core building blocks of AgentPool. This section covers the different types of nodes you can configure: agents (individual AI workers) and teams (coordinated groups).

## Agents

### Native Agents

[Native agents](agent.md) are the using regular client APIs of AI model providers and are backed by `Pydantic-AI`.

### ACP Agents

[ACP (Agent Communication Protocol) agents](acp-agents.md) integrate external coding agents like Claude Code, Gemini CLI, Codex, and many others. These run as separate processes and communicate via the ACP protocol. This means that AgentHub can act as a ACP bridge: ACP-Agents are connected to the AgentPool via ACP, can use some of its extended featureset and then also be controlled by the User via a UI ACP client such as `Zed` or `Toad`.

### AG-UI Agents

[AG-UI agents](agui-agents.md) connect to remote HTTP endpoints implementing the AG-UI protocol, enabling integration with any AG-UI compatible server.

### Claude Code Agents

[Claude Code agents](claude-code-agents.md) provide native integration with the Claude Agent SDK. While Claude Code can also be used via the ACP bridge, this direct integration offers lower latency, tighter integration, and the ability to expose AgentPool's internal toolsets to Claude Code via MCP.

### Codex Agents

[Codex agents](codex-agents.md) integrate with the Codex app-server via its JSON-RPC protocol. Codex provides advanced code editing capabilities with configurable reasoning effort levels and tool approval policies. Like Claude Code agents, Codex agents can leverage AgentPool's internal toolsets through MCP bridging.

### T3 Code Agents

[T3 agents](t3-agents.md) run their turns as threads in a T3 Code environment, which drives Claude Code, Codex, Cursor and other coding agents on its host, with worktrees and checkpoints. They connect to the environment's MCP endpoint, locally or remotely.

## Teams

[Teams](./team.md) coordinate multiple agents to work together on complex tasks:

- **Parallel teams**: Members execute simultaneously
- **Sequential teams**: Members execute in sequence, passing results along
- **Nested teams**: Teams can contain other teams for complex workflows

## Quick Comparison

| Feature | Standard Agent | Claude Code Agent | Codex Agent | ACP Agent | AG-UI Agent | Team | T3 Agent |
|---------|---------------|-------------------|-------------|-----------|-------------|------|----------|
| Runs in-process | Yes | Yes (SDK) | Yes (subprocess) | No | No | Yes | No |
| Full configuration | Yes | Yes | Yes | Limited | Limited | Yes | Limited |
| External tools | Via toolsets | Built-in + MCP bridge | Native + MCP bridge | Built-in | Via server | Via members | The provider's |
| Structured output | Result tools | Result tools | JSON Schema | Limited | Limited | Via members | No |
| Reasoning control | Model-dependent | Extended thinking | Effort levels | Model-dependent | Model-dependent | Via members | Model options |
| Streaming | Yes | Yes | Yes | Yes | Yes | Yes | Polled |
| Use case | Primary agents | Claude coding | Codex coding | External agents | Remote services | Coordination | Agents in T3 Code |
