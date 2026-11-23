---
title: MCP Server
description: Expose AgentPool tools via Model Context Protocol
icon: material/connection
---

# MCP Server

The MCP (Model Context Protocol) server exposes AgentPool tools and resources to external MCP clients, enabling other agents and applications to use your capabilities.

## What is MCP?

MCP is a standardized protocol for AI tool integration:

- **Tools** - Functions that agents can call
- **Resources** - Data and files agents can read
- **Prompts** - Reusable prompt templates (not served by AgentPool)

## Quick Start

```bash
# Expose tools from a pool
agentpool serve-mcp config.yml

# Or expose specific toolsets
agentpool serve-mcp config.yml --toolset file_access --toolset search
```

See [`serve-mcp`](../cli/serve-mcp.md) for all CLI options.

## Use Cases

### 1. Share Tools with Claude Code

Expose your custom tools to Claude Code:

```bash
# Start MCP server
agentpool serve-mcp tools.yml --transport sse --port 3001
```

Configure Claude Code's `mcp.json`:

```json
{
  "mcpServers": {
    "agentpool": {
      "url": "http://localhost:3001/sse"
    }
  }
}
```

### 2. Agent Composition

Let external agents use your pool's capabilities:

```yaml
# config.yml - Your pool with specialized agents
agents:
  data_expert:
    type: native
    model: openai:gpt-4o
    system_prompt: "Expert in data processing"
    
  api_expert:
    type: native
    model: anthropic:claude-sonnet
    system_prompt: "Expert in API design"
    
tools:
  - type: subagent  # Exposes task, list_available_nodes
```

External agents can now delegate to your experts via MCP.

### 3. Share Custom Tools

Expose custom Python functions:

```python
from agentpool import Agent
from agentpool.tools import tool

@tool
def analyze_data(data: str) -> str:
    """Analyze the provided data."""
    # Your analysis logic
    return f"Analysis result: {result}"

agent = Agent("analyzer", model="...", tools=[analyze_data])
```

```bash
agentpool serve-mcp --config agent.yml
```

## Exposed Capabilities

### Tools

All enabled tools from configured toolsets:

```yaml
tools:
  - type: file_access    # read, write, list_directory
  - type: process_management      # run_command, run_python
  - type: search         # web_search, news_search
  - type: subagent       # task, list_available_nodes
```

### Resources

Resources from configured resource providers:

```yaml
resources:
  - type: file
    path: /data
    pattern: "*.json"
```

## Architecture

```mermaid
graph LR
    subgraph Clients["MCP Clients"]
        Claude["Claude Code"]
        Other["Other Agents"]
    end
    
    subgraph Server["AgentPool MCP Server"]
        MCP["MCP Protocol"]
        Tools["Tool Manager"]
        Resources["Resources"]
    end
    
    subgraph Pool["Agent Pool"]
        Agents["Agents"]
        Toolsets["Toolsets"]
    end
    
    Claude <-->|MCP| MCP
    Other <-->|MCP| MCP
    MCP --> Tools
    MCP --> Resources
    Tools --> Toolsets
    Resources --> Pool
```

## Transport Types

### stdio (Default)

For subprocess communication:

```bash
agentpool serve-mcp config.yml --transport stdio
```

Used when the client spawns the server as a subprocess.

### SSE (Server-Sent Events)

For HTTP-based communication:

```bash
agentpool serve-mcp config.yml --transport sse --port 3001
```

## Background Tasks

An agent or team run can take minutes, and a normal tool call keeps the request open the
whole time. That can hit client timeouts, and over HTTP the session can idle out mid-run.
With background tasks ([SEP-2663](https://modelcontextprotocol.io/extensions/tasks/overview))
a client gets a task ID right away, can disconnect, and fetches the result later.

```bash
pip install "agentpool[mcp-tasks]"
agentpool serve-mcp config.yml --transport streamable-http --background-tasks
```

Agent and team tools then run as tasks when the client asks for it. That needs a client on the
`2026-07-28` protocol that supports the tasks extension, such as the FastMCP client. All other
clients, including ones that don't support tasks, keep calling the tools synchronously.
Cancelling a task cancels the agent run.

Task state lives in memory by default and is lost on restart. Pass `--tasks-url
redis://localhost:6379/0` (or set `FASTMCP_DOCKET_URL`) to keep it in Redis or Valkey.
A task's state and result are kept for 15 minutes by default, counted from when the task
starts, so the limit has to cover the run plus the time until the client fetches the result.
Raise it with `--tasks-ttl 7200` (seconds), or `tasks_ttl` on `MCPPoolServerConfig` when
building the server in Python. Tasks always run in the server's own process, because each
run needs the pool.

## Consuming MCP Servers

AgentPool can also **consume** MCP servers (act as client):

```yaml
agents:
  my_agent:
    mcp_servers:
      - type: stdio
        command: uvx
        args: ["some-mcp-server"]
        
      - type: streamable-http
        url: http://localhost:3001/mcp
```

See [MCP Server Integration](../advanced/mcp-servers.md) for consuming MCP servers.

## See Also

- [ACP Server](acp-server.md) - For IDE integration
- [OpenCode Server](opencode-server.md) - For OpenCode clients
- [MCP Server Integration](../advanced/mcp-servers.md) - Consuming MCP servers
