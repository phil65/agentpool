---
title: ACP Server
description: Agent Client Protocol server for IDE integration
icon: material/desktop-tower
---

# ACP Server

The ACP (Agent Client Protocol) server enables AgentPool to integrate with desktop applications and IDEs like Zed that support the protocol.

## What is ACP?

ACP is a standardized JSON-RPC 2.0 protocol that provides:

- Bidirectional communication between editor and agent
- Session management and conversation history
- File system operations with permission handling
- Terminal integration for command execution
- Tool confirmation modes for different workflows

## Quick Start

```bash
# Using uvx (no installation required)
uvx --python 3.13 agentpool@latest serve-acp config.yml

# Or with installed package
agentpool serve-acp config.yml
```

See [`serve-acp`](../cli/serve-acp.md) for all CLI options.

## IDE Configuration

### Zed Editor

Add to your Zed `settings.json`:

```json
{
  "agent_servers": {
    "AgentPool": {
      "command": "uvx",
      "args": [
        "--python", "3.13",
        "agentpool[coding]@latest",
        "serve-acp",
        "path/to/config.yml"
      ],
      "env": {
        "OPENAI_API_KEY": "your-api-key"
      }
    }
  }
}
```

### Other IDEs

For IDEs that support ACP:

1. Set command to `agentpool` or `uvx agentpool@latest`
2. Add `serve-acp` as first argument
3. Specify your configuration file
4. Add CLI options as needed
5. Set required environment variables

## Agent Configuration

```yaml
# config.yml
agents:
  assistant:
    type: native
    model: openai:gpt-4o
    display_name: "AI Assistant"
    system_prompt: "You are a helpful coding assistant."
    tools:
      - type: file_access
      - type: process_management
      - type: search
```

## Tool Confirmation Modes

The IDE's mode selector controls tool confirmation behavior:

| Mode | Behavior |
|------|----------|
| Auto-approve | Tools execute without confirmation |
| Confirm destructive | Only confirm writes, deletes, commands |
| Confirm all | Confirm every tool execution |

## The ACP Bridge

AgentPool acts as **both** an ACP server and client:

```mermaid
graph LR
    IDE["IDE (Zed)"] <-->|ACP| AP["AgentPool"]
    AP <-->|ACP| Claude["Claude Code"]
    AP <--> Pool["Agent Pool"]
```

This enables:

- **IDE → AgentPool**: IDE connects to your agents
- **AgentPool → External Agents**: Delegate to Claude Code, Codex, etc.
- **Unified orchestration**: Mix internal and external agents

## Remote Clients (Streamable HTTP, experimental)

Besides stdio and WebSocket, `serve-acp` can serve ACP over HTTP, following ACP's
"Streamable HTTP & WebSocket Transport" RFD (Streamable HTTP and WebSocket on one
`/acp` endpoint, compatible with the TypeScript SDK's experimental clients):

```bash
# Local only (no token needed on a loopback host)
agentpool serve-acp config.yml --transport http --port 8765

# Reachable from other machines: clients must send "Authorization: Bearer <token>"
AGENTPOOL_ACP_TOKEN=... agentpool serve-acp config.yml --transport http --host 0.0.0.0

# Plain WebSocket (ws://host:8765), same token rules
AGENTPOOL_ACP_TOKEN=... agentpool serve-acp config.yml --transport websocket --host 0.0.0.0
```

Agents run their tools on the serving machine, so `--transport websocket` and
`--transport http` follow the same rules:

- Binding to a non-loopback host requires `--token` (or `AGENTPOOL_ACP_TOKEN`);
  `serve-acp` refuses to start otherwise. On loopback the token is optional.
- Clients send the token as `Authorization: Bearer <token>`, for WebSocket on the
  upgrade request (e.g. `acptui connect ws://host:8765 -H "Authorization: Bearer ..."`).
  A missing or wrong token is refused with 401 before any ACP message. Tokens in query
  parameters are not accepted.
- Requests with an `Origin` header (browsers) are refused with 403 unless the origin is
  passed with `--allow-origin` (repeatable). Browsers apply no CORS to WebSockets, so
  this keeps web pages from driving a local agent. Clients that send no `Origin`
  (editors, CLIs) are not affected. Browsers cannot set headers on a WebSocket: browser
  clients use `--transport http`.

Put TLS in front (reverse proxy) when exposing it. Editors that only launch local agents can connect through
`uvx --from "aclientp[http2,websocket]" aclientp-connect <url> -H "Authorization: Bearer ${TOKEN}"`
(`http://…/acp` or `ws://…/acp`),
and other AgentPool instances with a [remote ACP agent](../configuration/node-types/acp-agents.md).

## External ACP Agents

Integrate external ACP agents into your pool:

```yaml
agents:
  claude:
    type: acp
    provider: registry       # resolved from the ACP registry and installed on demand
    registry_id: claude-acp
    display_name: "Claude Code"

  my_agent:
    type: acp
    provider: custom         # any ACP server command
    command: my-acp-server
    args: ["--stdio"]

  coordinator:
    type: native
    model: openai:gpt-5-mini
    tools:
      - type: subagent       # can delegate to claude and my_agent
```

`registry_id` is an agent ID of the [ACP registry](https://agentclientprotocol.com/registry)
(for example `claude-acp`, `codex-acp`, `gemini`, `goose`, `opencode`, `fast-agent`, `kimi`).
`provider: remote` connects to an ACP agent served over HTTP or WebSocket instead.

## Execution Environments

The tools AgentPool serves to an ACP agent (and the agent's file and terminal requests) can
run in another [execution environment](../configuration/execution-environments.md):

```yaml
agents:
  claude_docker:
    type: acp
    provider: registry
    registry_id: claude-acp
    environment:
      type: docker
      image: python:3.13-slim

  claude_e2b:
    type: acp
    provider: registry
    registry_id: claude-acp
    environment:
      type: e2b
      template: python-sandbox
```

## Toolset Bridging

ACP agents that support MCP get AgentPool's tools through an MCP bridge:

```yaml
agents:
  claude_orchestrator:
    type: acp
    provider: registry
    registry_id: claude-acp
    tools:
      - type: subagent        # Delegation tools
      - type: search          # Web search
        web_search: tavily
```

## Permissions

```yaml
agents:
  restricted:
    type: acp
    provider: registry
    registry_id: claude-acp
    allow_file_operations: false
    allow_terminal: false
    auto_approve: false
```

## See Also

- [OpenCode Server](opencode-server.md) - REST/SSE server for OpenCode clients
- [MCP Server](mcp-server.md) - Expose tools via MCP
- [External ACP Agents](../configuration/node-types/acp-agents.md) - Detailed agent configuration
