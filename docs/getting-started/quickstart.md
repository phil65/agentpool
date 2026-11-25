---
title: Quickstart
description: Quick introduction to using AgentPool
icon: material/rocket-launch
---

# Quickstart Guide

## ACP Integration (Recommended)

The fastest way to get started is through the **Agent Client Protocol (ACP)**, which integrates agentpool directly into your IDE.

### One-Line Setup

No installation needed - run directly with uvx:

```bash
uvx --python 3.13 agentpool@latest serve-acp agents.yml
```

### IDE Configuration (Zed)

Add to your Zed `settings.json`:

```json
{
  "agent_servers": {
    "AgentPool": {
      "command": "uvx",
      "args": [
        "--python", "3.13",
        "agentpool@latest",
        "serve-acp",
        "path/to/your/agents.yml"
      ],
      "env": {
        "OPENAI_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

Your agents now appear as modes in Zed's agent panel - switch between them mid-conversation.

### Wrap External Agents

Coding agents like Claude Code, Codex or any ACP agent become members of your pool:

```yaml
agents:
  claude:
    type: claude_code       # Claude Code via its SDK
    model: sonnet
  codex:
    type: codex             # Codex app-server
  goose:
    type: acp               # any ACP agent from the ACP registry
    provider: registry
    registry_id: goose
```

See [Node Types](../configuration/node-types/index.md) and
[ACP Integration](../advanced/acp-integration.md) for details.

## Other Clients

The same pool can be served to other clients:

```bash
agentpool serve-opencode agents.yml   # OpenCode TUI / desktop (opencode --server ...)
agentpool ui opencode agents.yml      # start the server and the OpenCode TUI together
agentpool serve-mcp agents.yml        # agents as MCP tools (Claude Code, Cursor, ...)
agentpool serve-agui agents.yml       # AG-UI frontends
agentpool serve-api agents.yml        # OpenAI-compatible API
```

## CLI Usage

```bash
# Create a starter configuration (agentpool.yml in the project)
agentpool config init

# Run an agent once
agentpool run assistant "What is Python?"

# Register a config file under a name and make it the active one
agentpool add myagents agents.yml
agentpool set myagents
agentpool list-agents
```

See the [CLI reference](../cli/index.md) for all commands.

## Configured Agents

Create an agent configuration:

```yaml
# agents.yml
agents:
  assistant:
    display_name: "Technical Assistant"
    model: openai:gpt-5-mini
    system_prompt: You are a helpful technical assistant.
    tools:
      - type: file_access
```

Use it in code:

```python
from agentpool import AgentPool


async def main():
    async with AgentPool("agents.yml") as pool:
        agent = pool.get_agent("assistant")
        response = await agent.run("What is Python?")
        print(response.content)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
```

## Functional Interface

Quick model interactions without configuration:

```python
from pydantic import BaseModel

from agentpool.functional import run_agent, run_agent_sync


class Analysis(BaseModel):
    summary: str
    key_points: list[str]


async def main():
    text = await run_agent("Analyze this text", model="openai:gpt-5-mini")
    print(text)

    # With structured output
    result = await run_agent("Analyze the sentiment", model="openai:gpt-5-mini", output_type=Analysis)
    print(f"Summary: {result.summary}")


# Sync usage (convenience wrapper)
text = run_agent_sync("Quick question", model="openai:gpt-5-mini")
```
