---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool/models/codex_agents.py
title: Codex Agents
description: Codex app-server integration
icon: material/robot
---

Codex agents provide AgentPool's integration with the Codex app-server, enabling advanced code editing, terminal access, and tool execution through Codex's JSON-RPC protocol.

## Overview

AgentPool integrates directly with Codex app-server via its JSON-RPC protocol, providing:

- **Native Codex features**: Full access to Codex's reasoning, tools, and model capabilities
- **Low latency**: Direct process communication via stdin/stdout
- **Toolset bridging**: Expose AgentPool's internal toolsets to Codex via MCP
- **Streaming events**: Real-time event propagation from Codex to AgentPool
- **Structured outputs**: Support for typed response schemas via JSON Schema

Codex agents are ideal for:

- Complex coding tasks with extended reasoning
- Terminal operations and shell command execution
- Multi-file refactoring and code generation
- Tasks benefiting from Codex's reasoning effort levels

## Configuration Reference

/// mknodes
{{ "agentpool.models.codex_agents.CodexAgentConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False) }}
///

## Examples

### Basic Configuration

A minimal Codex agent for coding tasks:

```yaml
agents:
  codex_coder:
    type: codex
    environment: /path/to/project  # working directory (or an execution environment config)
    model: gpt-5.1-codex-max
    reasoning_effort: medium
```

### Reasoning Effort

Control how deeply Codex reasons about tasks (the levels the model offers, for example
`low`, `medium`, `high`):

```yaml
agents:
  quick_helper:
    type: codex
    model: gpt-5.1-codex-max
    reasoning_effort: low  # Fast responses

  deep_thinker:
    type: codex
    model: gpt-5.1-codex-max
    reasoning_effort: high  # Complex problem solving
```

### Approval Policy and Sandbox

`approval_policy` decides when Codex asks before running a tool: `never` (default for
programmatic use), `on-request` (the model asks when it wants to) or `untrusted` (ask for
everything not known to be safe). Approvals reach the user through AgentPool's input
provider / the connected protocol client. `sandbox` limits file access (`read-only`,
`workspace-write`, `danger-full-access`).

```yaml
agents:
  auto_executor:
    type: codex
    approval_policy: never

  careful:
    type: codex
    approval_policy: untrusted
    sandbox: read-only
```

### Toolset Integration

Expose AgentPool's tools and toolsets to Codex via an MCP bridge:

```yaml
agents:
  codex_coordinator:
    type: codex
    environment: /path/to/project
    tools:
      - type: subagent  # delegate to the pool's other agents
      - type: search
        web_search: tavily
      - my_module.my_tool  # a function as a tool
```

### Structured Output

Reference a [response definition](../responses.md):

```yaml
responses:
  bug_report:
    response_schema:
      type: inline
      fields:
        title:
          type: str
        severity:
          type: str
          description: "critical, high, medium or low"
        affected_files:
          type: list[str]

agents:
  bug_finder:
    type: codex
    output_type: bug_report
```

### MCP Servers

```yaml
agents:
  codex_with_fs:
    type: codex
    mcp_servers:
      - "uvx mcp-server-filesystem /path/to/workspace"  # stdio server shorthand
      - type: streamable-http
        url: http://localhost:8000/mcp
```

### Passing Codex Options Through

`thread_options` passes parameters of Codex's `thread/start` request through unchanged (also
on resume and fork), for options AgentPool does not model itself.

## Runtime Switching

Model, reasoning effort and approval policy are config options of the agent: protocol clients
(ACP, OpenCode) show them as switchable settings, and code can change them:

```python
from agentpool import AgentPool

async with AgentPool("config.yml") as pool:
    agent = pool.get_agent("codex_coder")
    for model in await agent.get_available_models() or []:
        print(model.id)
    await agent.set_model("gpt-5.1-codex-mini")
    await agent.set_mode("high", category_id="reasoning_effort")
```

## Comparison with Claude Code Agents

| Feature | Codex Agent | Claude Code Agent |
|---------|-------------|-------------------|
| Protocol | Codex JSON-RPC | Claude Agent SDK |
| Reasoning control | Reasoning effort levels | Reasoning effort (adaptive thinking) |
| Tool approval | Approval policies (never / on-request / untrusted) | Permission modes |
| Models | OpenAI (Codex) models | Claude models |

Choose Codex agents for:

- Access to Codex-specific models and reasoning
- Projects already using Codex
- Tasks benefiting from Codex's reasoning effort system

Choose Claude Code agents for:

- Pure Claude model access
- Extended thinking capabilities
- Tighter Claude ecosystem integration

## Related Configuration

- [Claude Code Agents](claude-code-agents.md) - Claude Agent SDK integration
- [ACP Agents](acp-agents.md) - ACP protocol integration
- [Toolsets](../toolsets/index.md) - Tool collections for agents
- [Responses](../responses.md) - Structured output types
