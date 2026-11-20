---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool/models/claude_code_agents.py
title: Claude Code Agents
description: Native Claude Agent SDK integration
icon: material/robot
---

Claude Code agents provide AgentPool's native integration with the Claude Agent SDK, enabling file operations, terminal access, and advanced code editing capabilities through Claude's official tooling.

## Overview

While Claude Code can be used via the [ACP bridge](acp-agents.md), AgentPool provides its own complete integration using the Claude Agent SDK directly. This approach offers several advantages:

- **Lower latency**: No ACP protocol overhead between AgentPool and Claude Code
- **Tighter integration**: Direct access to Claude Code's streaming, tools, and events
- **Toolset bridging**: Expose AgentPool's internal toolsets to Claude Code via MCP
- **Better error handling**: Native exception propagation and recovery
- **Structured outputs**: Full support for typed response schemas

Claude Code agents are ideal for:

- Complex coding tasks requiring file system access
- Terminal operations and shell command execution
- Multi-file refactoring and code generation
- Tasks that need deep reasoning

## Configuration Reference

/// mknodes
{{ "agentpool.models.claude_code_agents.ClaudeCodeAgentConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False) }}
///

## Examples

### Basic Configuration

A minimal Claude Code agent for coding tasks:

```yaml
agents:
  coder:
    type: claude_code
    model: sonnet
```

### Custom System Prompt

Add custom instructions while keeping Claude Code's built-in system prompt:

```yaml
agents:
  python_dev:
    type: claude_code
    model: sonnet
    system_prompt:
      - "You are a Python expert focused on clean, maintainable code."
      - "Always include type hints and docstrings."
      - "Prefer pytest for testing."
```

To replace the built-in prompt entirely:

```yaml
agents:
  minimal_agent:
    type: claude_code
    include_builtin_system_prompt: false
    system_prompt: "You are a focused code reviewer. Only suggest improvements."
```

### Tool Restrictions

Control which tools the agent can use:

```yaml
agents:
  reader:
    type: claude_code
    allowed_tools:
      - Read
      - Grep
      - Glob
    disallowed_tools:
      - Bash
      - Write
      - Edit
```

### Reasoning Effort

Claude thinks adaptively; the effort sets how deeply (`low`, `medium`, `high`, `xhigh`, `max`):

```yaml
agents:
  architect:
    type: claude_code
    model: opus
    reasoning_effort: max
    system_prompt: "You are a software architect. Think deeply about design decisions."
```

Clients can change the effort (and fast mode, on models that have it) while the agent runs.

### Toolset Integration

Expose AgentPool's internal toolsets to Claude Code via MCP:

```yaml
agents:
  coordinator:
    type: claude_code
    tools:
      - type: subagent
        agents:
          - researcher
          - writer
      - type: agent_management
      - type: resource_access
```

This allows Claude Code to delegate tasks to other AgentPool agents.

### Structured Output

Define typed responses:

```yaml
responses:
  code_review:
    type: object
    properties:
      issues:
        type: array
        items:
          type: object
          properties:
            file: { type: string }
            line: { type: integer }
            severity: { type: string, enum: [error, warning, info] }
            message: { type: string }
      summary: { type: string }
      approved: { type: boolean }

agents:
  reviewer:
    type: claude_code
    output_type: code_review
    system_prompt: "Review the code and provide structured feedback."
```

### Environment Variables

Set environment variables for the agent:

```yaml
agents:
  secure_agent:
    type: claude_code
    env_vars:
      ANTHROPIC_API_KEY: ""  # Empty forces subscription usage
      DEBUG: "1"
      CUSTOM_VAR: "value"
```

## Permission Modes

Claude Code agents support different permission handling modes:

| Mode | Description |
|------|-------------|
| `default` | Ask for permission on each tool use |
| `acceptEdits` | Auto-accept file edits, ask for other operations |
| `plan` | Plan-only mode, no execution (safe for exploration) |
| `bypassPermissions` | Skip all permission checks (use with caution) |

Example:

```yaml
agents:
  planner:
    type: claude_code
    permission_mode: plan
    system_prompt: "Analyze the codebase and create an implementation plan."
```

## Toolset Integration

One of the key features of Claude Code agents is the ability to expose AgentPool's internal toolsets via MCP. When you configure toolsets, they are:

1. Started as an in-process MCP server
2. Connected to Claude Code using the SDK's native MCP support
3. Available as tools within Claude Code's tool palette

This enables powerful workflows:

```yaml
agents:
  smart_coder:
    type: claude_code
    tools:
      # Delegate to specialized agents
      - type: subagent
        agents:
          - test_writer
          - documentation_writer
      
      # Access shared resources
      - type: resource_access
      
      # Manage other agents
      - type: agent_management
```

The toolsets communicate directly with Claude Code without HTTP overhead.

## Comparison with ACP Agents

| Feature | Claude Code Agent | ACP Agent (Claude Code) |
|---------|------------------|-------------------------|
| Protocol overhead | None (direct SDK) | ACP protocol layer |
| Toolset integration | Native MCP bridge | Limited |
| Streaming | Full native support | Via ACP transport |
| Error handling | Native exceptions | Protocol-wrapped |
| Configuration | Full control | Limited to ACP options |
| Use case | Primary integration | Interop with other ACP clients |

Choose Claude Code agents for:

- Maximum performance and integration depth
- When you need toolset bridging
- Primary AgentPool deployments

Choose ACP agents when:

- Interoperating with other ACP clients (Zed, Toad)
- Using Claude Code alongside other ACP-compatible agents
- Building ACP-first architectures

## Related Configuration

- [ACP Agents](acp-agents.md) - ACP protocol integration (alternative approach)
- [Toolsets](../toolsets/index.md) - Tool collections for agents
- [System Prompts](../prompts.md) - Prompt configuration
- [Responses](responses.md) - Structured output types
