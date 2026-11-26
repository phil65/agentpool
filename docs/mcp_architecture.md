# MCP & Tool Architecture Overview

## Core Components

### MCPManager (`agentpool.mcp_server.manager`)
Manages MCP server **lifecycle** only. Spawns MCP server processes, wraps each in an
`MCPResourceProvider`, and aggregates them into an `AggregatingResourceProvider`.
Does NOT wire providers into ToolManager — that happens externally.

Lives on `MessageNode` (every agent has one) and on `AgentPool` / `BaseTeam`.

### ToolManager (`agentpool.tools.manager`)
Manages local tools + external resource providers. MCP tools become available when
someone adds the MCPManager's aggregating provider to the ToolManager.

### ToolManagerBridge (`agentpool.mcp_server.tool_bridge`)
The inverse of MCPManager: exposes an agent's internal ToolManager as an HTTP MCP
server. Used by wrapped agents (Claude Code, Codex, ACP) so the external process
can call back into our tools. It listens on 127.0.0.1, where any local process can
reach it, so it serves only requests carrying its random bearer token
(`ToolManagerBridge.headers`); every backend's MCP server config carries them.

---

## Provider Wiring — Three Layers

MCPManager only handles lifecycle. The **wiring** of providers into ToolManager
happens at three levels:

### 1. Agent-level (`self.mcp`)
Every MessageNode gets its own MCPManager from configured `mcp_servers`.
- **Native Agent**: Wires it in `__init__`:
  `self.tools.add_provider(self.mcp.get_aggregating_provider())` (`agent.py:263`)
- **Wrapped agents**: MCPManager is started (lifecycle runs) but **nobody wires**
  the providers into anything. The providers exist but are unused.

### 2. Pool-level (`pool.mcp`)
Pool has its own MCPManager for pool-wide MCP servers (from manifest).
In `pool.__aenter__()`, adds its aggregating provider to **every** agent's
ToolManager (`pool.py:176`). Removed in `pool.__aexit__()`.

- Works for Native Agents (they use ToolManager for LLM calls).
- Added to wrapped agents' ToolManager too, but **effectively invisible** — they
  don't use ToolManager for their LLM calls.

### 3. Team-level (`team.mcp`)
Teams have their own MCPManager. In `_on_node_added()`, adds the team's aggregating
provider to Native Agents' ToolManagers (`base_team.py:102`).

- Only wired for `Agent` instances (isinstance check).
- Wrapped agents in teams don't get team MCP tools.

---

## Per-Agent Breakdown

### Native Agent (`Agent`)
- **MCP connections**: MCPManager on MessageNode. Aggregating provider wired into
  ToolManager by the agent itself. Pool/team providers also added externally.
- **Tool exposure**: In-process. No bridge needed.
- **Dynamic changes**: Yes — ToolManager is live, tools/providers can be
  added/removed between calls.
- **Uses MCPManager providers**: Yes (all three layers).
- **Uses ToolBridge**: No.

### AG-UI Agent (`AGUIAgent`)
- **MCP connections**: MCPManager on MessageNode is started, but providers are
  **not wired** into the AG-UI request flow. Only `self.tools` (ToolManager)
  tools are converted to AG-UI format and sent per HTTP request.
- **Tool exposure**: Per-call schemas sent to remote AG-UI server. Client-side
  execution when the server calls them back.
- **Dynamic changes**: Can register ToolManager tools at runtime. MCP tool
  integration is incomplete — MCPManager tools don't flow into AG-UI requests.
- **Uses MCPManager providers**: No (started but not wired).
- **Uses ToolBridge**: No.

### Claude Code Agent (`ClaudeCodeAgent`)
- **MCP connections**: Does NOT use MessageNode's MCPManager for connections.
  Maintains `_mcp_servers: dict[str, McpServerConfig]` in Claude SDK format.
  External configs converted at startup. All passed to SDK client at connection
  time — Claude Code handles MCP connections natively.
- **Tool exposure**: Via **ToolManagerBridge** — starts an HTTP MCP server that
  the Claude Code process connects to as one of its MCP servers.
- **Dynamic changes**: SDK supports `set_mcp_servers` control request for dynamic
  add/remove mid-session. `toolChanged` notifications from bridge don't work
  properly yet.
- **Uses MCPManager providers**: No (MessageNode's MCPManager runs but is ignored).
- **Uses ToolBridge**: Yes. Bridge config merged into `_mcp_servers` dict.

### Codex Agent (`CodexAgent`)
- **MCP connections**: Collects configs at startup: bridge config + external configs
  converted to Codex format. All passed to `CodexClient(mcp_servers=...)`.
  **Startup only.**
- **Tool exposure**: Via **ToolManagerBridge** (same pattern as Claude Code).
- **Dynamic changes**: None. Codex CLI (Rust) has no runtime MCP modification API.
- **Uses MCPManager providers**: No (MessageNode's MCPManager runs but is ignored).
- **Uses ToolBridge**: Yes. `get_codex_mcp_server_config()` returns Codex-format.

### ACP Agent (`ACPAgent`)
- **MCP connections**: Collects from bridge + `self.mcp.servers` (reads the config
  list from MessageNode's MCPManager, but not the providers). Converts to ACP format,
  passes in `NewSessionRequest(mcp_servers=...)`. **Per-session, not dynamic.**
- **Tool exposure**: Via **ToolManagerBridge**.
- **Dynamic changes**: None at MCP level. New session needed for changes.
- **Uses MCPManager providers**: No — only reads `.servers` config list.
- **Uses ToolBridge**: Yes. `get_mcp_server_config()` returns ACP-format.

### Teams (`BaseTeam`)
- **MCP connections**: Own MCPManager. Aggregating provider added to member
  Native Agents' ToolManagers when they join.
- **Delegation only**: Teams don't run tools themselves.
- **Wrapped agents**: Not wired (isinstance check for `Agent` only).

### Pool (`AgentPool`)
- **MCP connections**: Own MCPManager for pool-wide servers.
  Aggregating provider added to all agents' ToolManagers in `__aenter__`.
- **Effective for**: Native Agents only (wrapped agents have ToolManager but
  don't use it for LLM calls).

---

## Summary Table

| Agent       | MCP Connection     | Tool Exposure      | Dynamic MCP | MCPManager providers used | ToolBridge |
|-------------|--------------------|--------------------|-------------|---------------------------|------------|
| Native      | MCPManager         | In-process         | Yes (live)  | Yes (all 3 layers)        | No         |
| AG-UI       | MCPManager*        | Per-call schemas   | Partial**   | No (started, not wired)   | No         |
| Claude Code | SDK native         | ToolBridge → MCP   | Yes (SDK)   | No (started, ignored)     | Yes        |
| Codex       | Client startup     | ToolBridge → MCP   | No          | No (started, ignored)     | Yes        |
| ACP         | Session request    | ToolBridge → MCP   | No          | Config only***            | Yes        |
| Team        | MCPManager         | Delegates to members | N/A       | Yes (wires to members)    | No         |
| Pool        | MCPManager         | Wires to all agents | N/A        | Yes (wires to agents)     | No         |

\* AG-UI starts MCPManager but providers don't flow into AG-UI requests
\** AG-UI can add ToolManager tools dynamically, but MCP integration is incomplete
\*** ACP reads `self.mcp.servers` (config list) but doesn't use providers
\**** Codex supports `config/mcpServer/reload` (re-reads config from disk, rebuilds on next turn)

---

## Key Observations

1. **MCPManager = lifecycle only.** It spawns processes and creates providers.
   Wiring into ToolManager is done externally by the agent, pool, or team.

2. **Three wiring layers**: Agent-level, pool-level, team-level. Only Native Agent
   benefits from all three. Wrapped agents get pool/team providers added to their
   ToolManager but never use them.

3. **Wrapped agents bypass MCPManager providers.** Claude Code, Codex, and ACP
   all manage their own MCP config dicts/lists and pass them to external processes.
   The MessageNode MCPManager runs uselessly for them.

4. **ACP is a hybrid**: Reads `self.mcp.servers` (config list) but not providers.
   Other wrapped agents ignore MCPManager entirely.

5. **ToolBridge is the inverse of MCPManager**: MCPManager consumes external MCP
   servers (inbound tools). ToolBridge exposes our tools as an MCP server (outbound).

6. **AG-UI gap**: MCPManager providers are started but not available as AG-UI tools.

7. **Pool MCP is invisible to wrapped agents**: Pool adds its aggregating provider
   to every agent's ToolManager, but wrapped agents don't use ToolManager for
   LLM calls, so pool-level MCP tools are inaccessible to them.
