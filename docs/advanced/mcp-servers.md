---
title: MCP Servers
description: Advanced MCP server features
icon: material/server
---

# MCP Server Integration

## Overview

AgentPool supports integration with MCP (Model Control Protocol) servers to extend agent capabilities through standardized interfaces. Currently, we support tool integration with both stdio and SSE-based MCP servers.

## Configuration

MCP servers can be configured in two ways:

### String Configuration

Simple command-line style configuration:

```yaml
agents:
  my_agent:
    model: openai:gpt-5-mini
    mcp_servers:
      - "uvx python-mcp-server --arg1 value1"
      - "node js-mcp-server.js"
```

### Full Configuration

Detailed configuration with environment variables and options:

```yaml
agents:
  my_agent:
    model: openai:gpt-5-mini
    mcp_servers:
      - type: stdio
        command: "pipx"
        args: ["run", "python-mcp-server", "--debug"]
        env:
          MY_VAR: "value"
      - type: streamable-http
        url: "http://localhost:3001"
```

## Usage Example

```python
# Configure agent with MCP server
async with AgentPool("pool.yml") as pool:
    # MCP tools from YAML defined mcp servers are automatically available
    agent = self.get_agent("my_agent_from_yaml")
    result = await agent.run("Use MCP tool to process data")

    # Multiple servers
    async with Agent(
        name="agent_name",
        model="...",
        mcp_servers=[
            "uvx server1",
            "uvx server2 --debug"
        ]
    )) as agent:
        # Tools from both servers available
        result = await agent.run("Use tools from multiple servers")
```
