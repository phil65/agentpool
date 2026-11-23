# MCP background tasks for the agentpool MCP server

Status: idea, not started. Written 2026-11-19 against fastmcp 4.2.0.

## Goal

`agentpool serve-mcp` exposes every pool agent and team as an MCP tool
(`PoolResourceProvider.get_tools()` → `MessageNode.to_tool()`). An agent run can take
minutes. Today the `tools/call` request stays open for the whole run, which:

- hits client-side request timeouts on long runs,
- breaks on HTTP when the session idles out (fastmcp 4.1 adopted the SDK's 30 minute idle
  timeout for old-protocol HTTP sessions),
- gives the client no way to check status, detach and come back later.

MCP background tasks (SEP-2663, extension `io.modelcontextprotocol/tasks`) fix exactly this:
the call returns a task id immediately, the work runs in a worker, the client polls for the
result. There is already a `# TODO: set task=True?` in
`src/agentpool_server/mcp_server/server.py` (`_register_pool_tools`).

## How fastmcp does it

- The extension lives in a separate package: `fastmcp[tasks]` / `fastmcp-tasks`, built on
  Docket. It is **not installed** in our env today.
- Server side:

  ```python
  from fastmcp_tasks import TasksExtension

  mcp.add_extension(TasksExtension())          # url="memory://" by default
  mcp.tool(task=True)(fn)                       # == TaskConfig(mode="optional")
  ```

  A `task=True` tool without the extension registered raises at startup. Task tools must be
  async. Modes: `forbidden` (sync only), `optional` (task if the client opts in, sync
  otherwise), `required` (error for clients that don't opt in).
- Tasks only exist on `2026-07-28` connections. Old-protocol clients and clients that don't
  opt in per request get normal synchronous execution, so `optional` costs nothing for them.
- Backends: `memory://` (single process, ~250 ms pickup, lost on restart) or Redis/Valkey
  (persistent, multiple workers). Configured via `TasksExtension(url=..., concurrency=...)`
  or `FASTMCP_DOCKET_*` env vars. Results are kept for `FASTMCP_DOCKET_EXECUTION_TTL`
  (default 15 min).
- A server with the extension and at least one task tool starts an embedded worker.
- Progress: the `Progress` dependency (`set_total`, `increment`, `set_message`) works in both
  sync and task execution.
- Input mid-task: imperative `ctx.elicit()` raises inside task tools. The tool has to use the
  guard pattern instead: return an `InputRequiredResult`, get re-invoked with the answers on
  `ctx.input_responses` (the same Multi Round-Trip pattern as `docs/examples/mcp_elicitation`).
- Caller identity (access token, HTTP headers) is snapshotted and restored in the worker;
  plaintext in Redis unless `FASTMCP_TASKS_ENCRYPTION_KEY` is set.

## What it means for agentpool

### Server side (`serve-mcp`) — the main win

1. **Single process, embedded worker only.** Extra workers (`fastmcp_tasks.worker_cli`)
   would each need the full pool (agents, MCP connections, storage), and tools must be known
   at startup. So: `memory://` by default, Redis only for surviving restarts, never
   separate workers. That fits how we run the server anyway.
2. **Agent tools are re-entrant calls, not plain functions.** A task leg can end and be
   re-invoked (guard pattern). An agent run must not restart from scratch on re-entry, so
   anything beyond "run once, return the result" needs the run to live outside the tool call
   (keyed by task id), with the tool just attaching to it. For a first version: no mid-task
   input, the tool runs the agent once.
3. **Progress.** `MCPServer.report_progress` sends via `current_session`, which has no
   request behind it inside a worker. It must move to the `Progress` dependency (or
   whatever fastmcp offers for task tools) to keep working.
4. **User input / tool confirmation.** MCP-served agents can't ask the user today either
   (the server never elicits), so tasks don't make this worse. If we add it later it has to
   be guard-pattern based, which conflicts with point 2 unless runs live outside the call.
5. **Cancellation.** Not covered by the fastmcp docs. Needs checking: does a task cancel
   cancel the tool coroutine, and does `agent.run` clean up (subprocess agents like Claude
   Code / Codex must be interrupted, not just abandoned).
6. **Config.** Something like:

   ```yaml
   # MCPPoolServerConfig
   background_tasks: true          # register TasksExtension, agent/team tools task=optional
   tasks_url: memory://            # or redis://...
   ```

   Optional extra: `fastmcp[tasks]` goes into the `server` extra (or its own extra), import
   lazily so the core install doesn't need Docket.

### Client side (agents calling external MCP servers)

`MCPClient.call_tool` goes through `fastmcp.Client.call_tool`, which handles task polling
transparently once the client side of the tasks extension is active. To verify: whether the
client opts in automatically when `fastmcp-tasks` is installed or needs an explicit
extension, and how a long task interacts with our tool timeouts and with `ToolCallStartEvent`
/ progress events. Gain is smaller here: only matters for external servers that declare
`required` task tools (those currently fail for us with "missing required capability").

## Client support (reality check)

Only the FastMCP client is documented as supporting tasks end to end. Claude Code, Codex,
Zed etc. would just keep calling synchronously (`optional` mode). So the server-side work
pays off mainly for programmatic clients (FastMCP-based, other agentpool instances) until
hosts adopt the extension.

## Suggested order

1. Server: opt-in config flag, `TasksExtension` with `memory://`, agent/team tools
   `task=optional`, run-once semantics. Test with `fastmcp.Client` against a slow
   `TestModel` agent: task id returned, polling, result, TTL expiry.
2. Move progress reporting to the task-compatible API.
3. Verify and implement cancellation for native and subprocess agents.
4. Client side: confirm opt-in behaviour, support external `required` task tools.
5. Later, only if needed: runs that live outside the call, enabling mid-task input.

## References

- fastmcp docs: https://gofastmcp.com/docs/servers/tasks
- MCP tasks extension: https://modelcontextprotocol.io/extensions/tasks/overview
- `fastmcp/utilities/tasks.py` (`TaskConfig`, `TASKS_EXTENSION_ID`)
