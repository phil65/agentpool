---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool/models/opencode_v2_agents.py
title: OpenCode V2 Agents
description: Drive an OpenCode v2.3+ HTTP/SSE server as an AgentPool agent
icon: simple/opencode
---

OpenCode V2 agents connect to an [OpenCode](https://github.com/anomalyco/opencode) v2.3+ HTTP/SSE server (aligned with OpenCode v2.4.1 via `opencode_v2_sdk`), either by spawning a private `opencode serve` process automatically or by connecting to an already-running OpenCode V2 server (such as `opencode serve` or `agentpool serve-opencode`).

## How it works

- **Managed or remote server**: When `url` is omitted, AgentPool spawns `opencode serve --hostname 127.0.0.1 --port <free-port>` with an ephemeral basic-auth password, waits for `/api/info`, and shuts it down cleanly on exit. Set `url` (and optional `password`) to connect to a running server instead.
- **Live SSE streaming**: Each turn subscribes to `GET /api/event`, enqueues the prompt via `POST /api/session/{id}/prompt`, and translates V2 step, reasoning, text, tool, compaction, retry, and toast events into AgentPool's stream events.
- **Permissions and forms**: When `permission_mode: ask`, `permission.asked` events route through AgentPool's `InputProvider.get_tool_confirmation()` and reply via `POST /api/session/{id}/permission/{requestID}/reply`. Interactive `form.created` requests route through `InputProvider.get_elicitation()` and reply via `POST /api/session/{id}/form/{formID}/reply`.
- **MCP toolset bridging**: AgentPool `tools` and toolsets are exposed via an in-process Streamable HTTP MCP bridge (`ToolManagerBridge`) registered through `/api/experimental/mcp/{server}`, alongside any configured `mcp_servers`.
- **Session management & config options**: `list_sessions` and `load_session` read and replay projected V2 session messages; `mode` (permissions), `agent`, `thought_level` (model variant), and `model` are exposed as switchable config options.

## Configuration Reference

/// mknodes
{{ "agentpool.models.opencode_v2_agents.OpenCodeV2AgentConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False) }}
///

## Basic Usage

```yaml
agents:
  # Spawn and manage a private `opencode serve` process automatically
  opencode_local:
    type: opencode_v2
    model: anthropic/claude-sonnet-4-5
    variant: high
    permission_mode: ask
    tools:
      - type: subagent

  # Connect to an existing OpenCode V2 server
  opencode_remote:
    type: opencode_v2
    url: http://127.0.0.1:4096
    password: ${OPENCODE_PASSWORD}
    agent: build
    permission_mode: allow
```

## Configuration Notes

- `model` accepts `provider/model` (including provider models whose own ID contains `/`, such as OpenRouter IDs) or a bare model ID resolved against `/api/model`.
- `permission_mode` defaults to `allow` for programmatic use. Use `ask` for interactive tool confirmations or specify fine-grained glob rules via `permissions`.
- When connecting to a remote server that uses basic authentication, set `password: ${OPENCODE_PASSWORD}` (or export `OPENCODE_PASSWORD` / `OPENCODE_SERVER_PASSWORD`).
