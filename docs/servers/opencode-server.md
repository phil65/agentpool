---
title: OpenCode Server
description: REST/SSE server compatible with the OpenCode V2 API protocol
icon: material/code-braces
---

# OpenCode Server

The OpenCode server implements the [OpenCode](https://github.com/anomalyco/opencode) **V2** HTTP + SSE API protocol (pinned to v2.3.2), allowing the OpenCode V2 TUI, desktop, and web clients—or another AgentPool `opencode_v2` agent—to drive your AgentPool agents and teams.

## Quick Start

```bash
# Start the OpenCode V2 server
agentpool serve-opencode config.yml --port 4096

# Optional Basic auth (username: opencode)
agentpool serve-opencode config.yml --port 4096 --password secret
```

See [`serve-opencode`](../cli/serve-opencode.md) for all CLI options.

## Connecting Clients

### OpenCode TUI / CLI

Point OpenCode v2.3+ at your running server:

```bash
opencode --server http://127.0.0.1:4096
```

If Basic auth is enabled via `--password` or `OPENCODE_PASSWORD`, provide the same password to the client.

### AgentPool `opencode_v2` Agent

Another AgentPool instance can connect directly over HTTP/SSE:

```yaml
agents:
  remote_coder:
    type: opencode_v2
    url: http://127.0.0.1:4096
    password: "${OPENCODE_PASSWORD}"
```

## Programmatic Usage

```python
from agentpool import AgentPool
from agentpool_server.opencode_v2_server.server import OpenCodeV2Server


async def main() -> None:
    async with AgentPool("config.yml") as pool:
        server = OpenCodeV2Server(
            pool,
            host="127.0.0.1",
            port=4096,
            working_dir="/path/to/project",
        )
        await server.start()
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENCODE_PASSWORD` | HTTP Basic auth password (username `opencode`). Auth is disabled when unset. |
| `OPENCODE_SERVER_PASSWORD` | Alternative env var accepted for the Basic auth password. |

## See Also

- [OpenCode V2 Agents](../configuration/node-types/opencode-v2-agents.md) - Connect AgentPool to an OpenCode V2 server
- [ACP Server](acp-server.md) - For IDE integration
- [MCP Server](mcp-server.md) - Expose tools via MCP
