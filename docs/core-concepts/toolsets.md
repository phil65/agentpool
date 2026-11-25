---
title: Tools and Toolsets
description: How agents get their tools
icon: material/shield-check
---

## Tools, Toolsets and Resource Providers

An agent's capabilities come from its `tools` list. Each entry is one of:

- a **single tool**: a function given by import path, or a built-in AgentPool tool such as
  `bash`, `read`, `grep` or `question` ([Tools](../configuration/tools.md));
- a **toolset**: a group of related tools (file access, subagents, search, ...) configured
  as one entry ([Toolsets](../configuration/toolsets/index.md));
- a **built-in tool of the model provider** (`type: builtin`), executed by the provider
  itself, for example web search ([Built-in Tools](../configuration/builtin-tools.md)).

Internally every toolset is a `ResourceProvider`: an object that provides tools (and
optionally prompts and resources) to the agent. MCP servers are resource providers too, so
an agent's tools are the union of its tool configs, its toolsets and its MCP servers.

```yaml
agents:
  developer:
    model: openai:gpt-5-mini
    tools:
      - type: file_access          # toolset: read, write, edit, grep, ...
      - type: process_management   # toolset: background processes
      - type: subagent             # toolset: delegate to the pool's other agents
      - type: bash                 # single tool
      - type: import               # a function as a tool
        import_path: webbrowser.open
    mcp_servers:
      - "uvx mcp-server-git"
```

The same `tools` list works for Claude Code, Codex, ACP, OpenCode V2 and Antigravity agents:
for these, AgentPool serves the configured tools to the external agent over an MCP bridge.

## Toolsets and their Tools

The tools of each toolset (generated from the toolset classes):

/// mknodes
{{ "agentpool_config.toolsets.ToolsetConfig" | toolset_tools_table(skip=["composio"]) }}
///

## Tool Confirmation

Tools can require confirmation before they run. `requires_tool_confirmation` on the agent
decides whether that is asked (`per_tool`, the default, uses each tool's
`requires_confirmation`; `always` / `never` override it). Confirmations are asked through the
agent's input provider, which the protocol servers (ACP, OpenCode, AG-UI, ...) forward to
their client.

```yaml
agents:
  careful:
    model: openai:gpt-5-mini
    requires_tool_confirmation: per_tool
    tools:
      - type: bash
        requires_confirmation: true
      - type: read
```

## Code Mode

With `tool_mode: codemode` the agent's tools are wrapped in a Python execution environment:
the model writes code that calls the tools as functions, which lets it combine several tool
calls in one step.

```yaml
agents:
  scripted:
    model: openai:gpt-5-mini
    tool_mode: codemode
    tools:
      - type: file_access
```

## Tools in Python

Tools can also be passed as callables (or import path strings) when creating an agent, and
toolsets as `ResourceProvider` instances:

```python
from agentpool import Agent


def get_weather(city: str) -> str:
    """Get the weather for a city."""
    return f"Sunny in {city}"


async def main():
    async with Agent(model="openai:gpt-5-mini", tools=[get_weather]) as agent:
        result = await agent.run("What's the weather in Paris?")
        print(result.content)
```
