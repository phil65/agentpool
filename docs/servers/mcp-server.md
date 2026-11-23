---
title: MCP Server
description: Expose AgentPool tools via Model Context Protocol
icon: material/connection
---

# MCP Server

The MCP (Model Context Protocol) server makes the agents and teams of a pool callable from any
MCP client: Claude Code, other agents, or another AgentPool.

## What it Serves

- **A tool per agent and team**, `ask_<name>`, taking a `prompt` and returning the node's
  answer. By default all teams and the agents that are not team members.
- **The pool's skills**, through the MCP Skills extension (SEP-2640).
- **The pool's prompt library** as MCP prompts: `prompts.system_prompts` under their names,
  prompts of prompt hubs (`prompts.providers`) as `<provider>.<name>`. Claude Code offers them
  as `/mcp__<server>__<name>` commands.

## Quick Start

```bash
# Serve over stdio (for clients that spawn the server)
agentpool serve-mcp config.yml

# Serve over HTTP
agentpool serve-mcp config.yml --transport streamable-http --port 3001
```

See [`serve-mcp`](../cli/serve-mcp.md) for all CLI options.

## Configuration

The manifest's `pool_server` section sets the server up; options given to `serve-mcp`
override it:

```yaml
pool_server:
  type: mcp
  transport: streamable-http
  host: localhost
  port: 3001
  serve_nodes: [data_expert, api_expert]  # true: all (default), false: none
  serve_skills: true
  serve_prompts: true                     # or a list of prompt names, or false
  cors_origins: []                        # browser clients allowed in (HTTP only)
  auth: null                              # see Authentication below (HTTP only)
  serve_events: false                     # see Session Events below
  background_tasks: false                 # see Background Tasks below

agents:
  data_expert:
    type: native
    model: openai:gpt-5-nano
    system_prompt: "Expert in data processing"
  api_expert:
    type: native
    model: openai:gpt-5-nano
    system_prompt: "Expert in API design"
```

A `serve_nodes` list serves exactly the nodes it names, team members included.

`cors_origins` lets browser-based clients on those origins (for example the MCP Inspector at
`http://localhost:6274`) call the server: they get CORS headers and pass its origin check.
Empty by default: `"*"` would let any website open in a browser on the machine call the pool's
agents.

## Authentication

Over HTTP, anyone who can reach the server's address can use the pool's agents. Keep it on
`localhost`, or set `auth` (HTTP transports only; refused with stdio).

Tokens, one per client (sent as `Authorization: Bearer <token>`). For a trusted network or a
server behind a proxy: no sign-in, no expiry; remove a client's token to lock it out.

```yaml
pool_server:
  type: mcp
  transport: streamable-http
  auth:
    type: tokens
    tokens:
      zed: ${ZED_MCP_TOKEN}
      ci: ${CI_MCP_TOKEN}
```

JWTs from an identity provider (the server is an OAuth 2.1 resource server). Tokens are
checked against the provider's keys (`jwks_uri`, or one `public_key`) and must carry the
`issuer`, `audience` and `required_scopes` given. With `authorization_servers` (and the
server's public `base_url`), it publishes protected resource metadata (RFC 9728), so MCP
clients find where to sign in by themselves.

```yaml
pool_server:
  type: mcp
  transport: streamable-http
  auth:
    type: jwt
    jwks_uri: https://auth.example.com/.well-known/jwks.json
    issuer: https://auth.example.com
    audience: https://mcp.example.com/mcp
    required_scopes: [mcp:agents]
    authorization_servers: [https://auth.example.com]
    base_url: https://mcp.example.com
```

With tokens, Claude Code passes the header:
`claude mcp add --transport http agentpool http://localhost:3001/mcp --header "Authorization: Bearer $TOKEN"`.

## Use with Claude Code

```bash
agentpool serve-mcp config.yml --transport streamable-http --port 3001
claude mcp add --transport http agentpool http://localhost:3001/mcp
```

Claude Code can then ask `data_expert` and `api_expert` through `ask_data_expert` and
`ask_api_expert`.

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
Raise it with `--tasks-ttl 7200` (seconds), or `tasks_ttl: PT2H` in `pool_server`. Tasks
always run in the server's own process, because each run needs the pool.

## Session Events

With `serve_events: true` in `pool_server`, the server publishes the pool's session activity
through the MCP Events extension
([SEP-3415](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3415),
experimental), modelled on ACP v2's session updates:

| Event | Payload | When |
|---|---|---|
| `session.state_update` | `session_id`, `agent`, `state`; for `idle` also `stop_reason` (`end_turn`, `cancelled`, `error`), `usage`, `error` | a turn starts (`running`), waits for the user (`requires_action`), or ends (`idle`) |
| `session.info_update` | `session_id`, `agent`, `title`, `parent_id` | a session is created or gets its title |
| `session.usage_update` | `session_id`, `agent`, `used`, `size`, `cost`, `currency` | after each response: tokens in the context (`used`) of the model's window (`size`, `null` while unknown), the session's cost so far |
| `session.notice` | `session_id`, `agent`, `severity`, `title`, `description` | information for the user outside the conversation (rate limits, a changed agent definition, ...) |
| `session.compaction_update` | `session_id`, `agent`, `compaction_id`, `status` (`in_progress`, `completed`, `failed`), `trigger`, `pre_tokens`, `error` | a context compaction starts or ends |

Every payload has a `timestamp`. Both take the optional arguments `agent` and `session_id` to
follow one agent or session. A client that reconnects with its cursor gets what it missed, as
long as the pool still keeps it (the last 1000 entries); beyond that the server reports the gap.

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
