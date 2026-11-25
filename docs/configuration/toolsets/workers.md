---
title: Workers Toolset
description: Delegate tasks to other agents and teams
icon: material/account-hard-hat
---

# Workers Toolset

The Workers toolset creates tools that delegate tasks to other agents or teams in the pool. Each configured worker becomes an `ask_<name>` tool that the agent can use.

## Overview

Workers allow building hierarchies where a manager agent can delegate specialized tasks to worker agents or teams. This is useful for:

- **Task specialization**: Different agents handle different domains
- **Parallel processing**: Delegate multiple tasks to workers
- **Team coordination**: Use entire teams as workers for complex tasks

## Basic Usage

```yaml
agents:
  manager:
    model: openai:gpt-5-mini
    tools:
      - type: workers
        workers:
          - name: code_reviewer
            type: agent
          - name: researcher
            type: agent
            pass_message_history: true
```

This creates tools `ask_code_reviewer` and `ask_researcher` for the manager agent.

## Toolset Configuration

/// mknodes
{{ "agentpool_config.toolsets.WorkersToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools") }}
///

## Worker Types

### Agent Workers

Standard agents with history management options:

```yaml
tools:
  - type: workers
    workers:
      - name: helper_agent
        type: agent
        reset_history_on_run: true    # Clear history before each run (default)
        pass_message_history: false   # Don't share parent's context (default)
```

| Option | Default | Description |
|--------|---------|-------------|
| `reset_history_on_run` | `true` | Clear worker's conversation history before each run |
| `pass_message_history` | `false` | Pass parent agent's message history to worker |

### Team Workers

Use entire teams as workers:

```yaml
tools:
  - type: workers
    workers:
      - name: research_team
        type: team
```

Team workers return formatted output with all team member responses.

### ACP Agent Workers

Use external ACP-compatible agents (Claude Code, Gemini CLI, etc.):

```yaml
tools:
  - type: workers
    workers:
      - name: claude_code
        type: acp_agent
```

### AG-UI Agent Workers

Use remote AG-UI protocol servers:

```yaml
tools:
  - type: workers
    workers:
      - name: remote_agent
        type: agui_agent
```

## Worker Configuration Reference

/// mknodes
{{ "agentpool_config.workers.WorkerConfig" | union_to_markdown(display_mode="yaml", header_style="pymdownx") }}
///

## Example: Manager with Specialized Workers

```yaml
agents:
  code_expert:
    model: openai:gpt-4o
    system_prompt: "You are a code review specialist."

  research_expert:
    model: openai:gpt-4o
    system_prompt: "You are a research specialist."

  manager:
    model: openai:gpt-4o
    system_prompt: "You coordinate tasks between specialists."
    tools:
      - type: workers
        workers:
          - name: code_expert
            type: agent
          - name: research_expert
            type: agent
            pass_message_history: true  # Share context with researcher
```
