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

[Native agents](agent.md) call the APIs of AI model providers directly and are backed by `Pydantic-AI`.

### ACP Agents

[ACP (Agent Client Protocol) agents](acp-agents.md) integrate external coding agents like Claude Code, Gemini CLI, Codex, and many others. These run as separate processes and communicate via the ACP protocol. This means that AgentPool can act as an ACP bridge: ACP-Agents are connected to the AgentPool via ACP, can use some of its extended featureset and then also be controlled by the User via a UI ACP client such as `Zed` or `Toad`.

### AG-UI Agents

[AG-UI agents](agui-agents.md) connect to remote HTTP endpoints implementing the AG-UI protocol, enabling integration with any AG-UI compatible server.

### Claude Code Agents

[Claude Code agents](claude-code-agents.md) provide native integration with the Claude Agent SDK. While Claude Code can also be used via the ACP bridge, this direct integration offers lower latency, tighter integration, and the ability to expose AgentPool's internal toolsets to Claude Code via MCP.

### Codex Agents

[Codex agents](codex-agents.md) integrate with the Codex app-server via its JSON-RPC protocol. Codex provides advanced code editing capabilities with configurable reasoning effort levels and tool approval policies. Like Claude Code agents, Codex agents can leverage AgentPool's internal toolsets through MCP bridging.

### T3 Code Agents

[T3 agents](t3-agents.md) run their turns as threads in a T3 Code environment, which drives Claude Code, Codex, Cursor and other coding agents on its host, with worktrees and checkpoints. They connect to the environment's MCP endpoint, locally or remotely.

### Antigravity Agents

[Antigravity agents](antigravity-agents.md) integrate Google's `google-antigravity` Python SDK (`agentpool[antigravity]`). They run Antigravity's local harness with built-in coding tools, multimodal inputs, thinking levels, policy-driven tool approvals, native structured output, and MCP bridging for AgentPool toolsets.

### OpenCode V2 Agents

[OpenCode V2 agents](opencode-v2-agents.md) drive an OpenCode v2.3+ HTTP/SSE server (`opencode serve` or a remote V2 endpoint), streaming live steps, reasoning, text, tool calls, permissions, and forms while bridging AgentPool toolsets via MCP.

### File Agents

[File agents](file-agents.md) are defined in markdown files with YAML frontmatter (Claude Code
subagents, OpenCode agents or AgentPool's own format), listed under `file_agents`.

## Teams

[Teams](./team.md) coordinate multiple agents to work together on complex tasks:

- **Parallel teams**: Members execute simultaneously
- **Sequential teams**: Members execute in sequence, passing results along
- **Nested teams**: Teams can contain other teams for complex workflows

## Quick Comparison

| Feature | Standard Agent | Claude Code Agent | Codex Agent | Antigravity Agent | OpenCode V2 Agent | ACP Agent | AG-UI Agent | Team | T3 Agent |
|---------|---------------|-------------------|-------------|-------------------|-------------------|-----------|-------------|------|----------|
| Runs in-process | Yes | Yes (SDK) | Yes (subprocess) | Yes (SDK) | Subprocess / HTTP | No | No | Yes | No |
| Full configuration | Yes | Yes | Yes | Yes | Yes | Limited | Limited | Yes | Limited |
| External tools | Via toolsets | Built-in + MCP bridge | Native + MCP bridge | Built-in + MCP bridge | Built-in + MCP bridge | Built-in | Via server | Via members | The provider's |
| Structured output | Result tools | Result tools | JSON Schema | JSON Schema (`finish`) | No | Limited | Limited | Via members | No |
| Reasoning control | Model-dependent | Reasoning effort | Effort levels | Thinking levels | Model variants | Model-dependent | Model-dependent | Via members | Model options |
| Streaming | Yes | Yes | Yes | Yes | Yes (SSE) | Yes | Yes | Yes | Polled |
| Use case | Primary agents | Claude coding | Codex coding | Antigravity / Gemini coding | OpenCode v2.3+ coding | External agents | Remote services | Coordination | Agents in T3 Code |
