---
title: MCP Server integration
description: Model Context Protocol integration
icon: material/server
---

## Overview

AgentPool integrates with MCP (Model Context Protocol) servers to provide additional tools and functionality to agents.

## Defining MCP Servers

### In YAML Configuration

```yaml
# In agent configuration
agents:
  code_assistant:
    description: "Helps with coding tasks"
    model: openai:gpt-5
    mcp_servers:
      # Simple string form (stdio)
      - "python -m myserver --debug"

      # Full configuration
      - type: stdio
        command: python
        args: ["-m", "codetools"]
        env:
          PYTHONPATH: src
          DEBUG: "1"

      # Remote servers
      - type: streamable-http
        url: "http://localhost:3001/mcp"
      - type: sse
        url: "http://localhost:3002/sse"
```

### In Agent Constructor

```python
from agentpool import Agent
from agentpool_config.mcp_server import StdioMCPServerConfig

agent = Agent(
    model="openai:gpt-5-mini",
    mcp_servers=[
        # String form
        "python -m myserver",
        # Full configuration
        StdioMCPServerConfig(command="python", args=["-m", "codetools"], env={"DEBUG": "1"}),
    ],
)
```

## Initialization

MCP servers are connected when the agent enters its async context and closed when it exits:

```python
async with Agent(model="openai:gpt-5-mini", mcp_servers=servers) as agent:
    tools = await agent.tools.get_tools()  # includes the MCP servers' tools
    await agent.run("Use MCP tools...")
```

MCP tools become available like any other agent tool. Servers can also provide prompts,
resources and skills (MCP Skills extension), and send events (MCP Events extension, used by
`mcp_event` triggers). At runtime, `/mcp` commands (or `agent.set_mcp_server_enabled()` /
`agent.reconnect_mcp_server()`) enable, disable and reconnect an agent's servers.

Pool-level servers (the manifest's top-level `mcp_servers`) are shared by all agents.

## Configuration Options

See [MCP configuration](../configuration/mcp.md) for all options of the server types
(`stdio`, `sse`, `streamable-http`).
