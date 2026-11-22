# ACP v2 for the agentpool ACP server

Status: steps 1 (aclientp, unreleased) and 2 done; step 3 next. Written
2026-11-22 against aclientp 0.1.7, which pins the v2 schema at `schema-v2.0.0-alpha.17`.

## Goal

`agentpool serve-acp` answers ACP v1 and v2 clients from one process. T3 Code asks for v2
(and falls back to v1, see `T3_CODE_INTEGRATION.md`); Zed asks for v1. v2 is close to stable,
and more clients are moving to it.

## Decision: one server, two protocol adapters

Not a second server or CLI command. ACP picks the version per connection, in `initialize`:
the client sends the highest version it supports, and the agent chooses. Whoever configures
the command (`agentpool serve-acp config.yml`) cannot know which version a client will ask
for, so one binary must answer both. aclientp already has the router for this,
`aclientp.router.RoutedAgentConnection` (it mirrors the TypeScript SDK's
`AgentProtocolRouter`). It picks the v1 or the v2 agent on `initialize` and never re-routes.

The OpenCode split (`serve-opencode` / `serve-opencode-v2`) is no precedent: those are
two unrelated REST APIs for different clients, with nothing to negotiate. ACP v2 keeps v1's
session model, and most of its methods carry over by name.

## What changes in v2 (`aclientp.schema.v2`)

| | v1 | v2 |
|---|---|---|
| Prompt | `session/prompt` blocks until the turn ends | returns once the message is inserted (with its `messageId`); `state_update` running → idle / requires_action carries progress, and the stop reason travels on `idle`, including `"error"`. A JSON-RPC error means the message was not inserted. |
| Streaming | chunks without identity; separate `tool_call` and `tool_call_update` | every chunk carries a `messageId`; message upserts; a single `tool_call_update` upsert with patch semantics (omitted means unchanged, `null` clears); `tool_call_content_chunk` |
| Sessions | `session/load` replays history | `session/resume` with `replayFrom` covers resuming and replaying |
| Modes | `set_session_mode` plus config options | config options only |
| fs and terminals | the agent can use the client's fs (`fs/*`) and terminals | gone: terminals are agent-owned and display-only (`terminal_update`, `terminal_output_chunk`, `cancel_terminal`) |
| Auth | `authenticate` | `login` |
| New | | subagent sessions (`subagent_update`), background work on the idle state, permission requests with `title` and `subject`, `cancel_tool_call` |

Several of these fix open items from the T3 work. `stopReason: "error"` stops failed turns
from looking successful. Agent-owned terminals replace the bogus `command_execution`
items. Subagent sessions could surface agentpool subagents as T3 child threads. v2's turn
model (detached turns, background work, idle state) is also closer to agentpool's
queue, steer and background design than v1's request-scoped turn.

## Where the server splits

`src/agentpool_server/acp_server/` is about 4,100 lines.

- **Protocol-neutral, shared:** `session_manager.py`, most of `session.py` (agent, cwd, MCP
  servers, commands, storage, running the turn), `commands/`.
- **One per version:**
  - the request handlers (`acp_agent.py` for v1; a v2 class on `aclientp.v2.agent.Agent`)
  - the event converter (stream events → v1 updates, or v2 upserts with ids and state updates)
  - the permission and elicitation shapes (`input_provider.py`)
  - content conversion
- **Refactor needed:** `session.py` mixes the core with v1 I/O (`ACPNotifications`,
  `ACPRequests`, `ACPFileSystem`, `process_prompt` returning a `StopReason`). The core should
  model a turn as a task with states (running / idle / requires_action / error) and emit
  through a small outbox interface. The v1 adapter folds that into the blocking
  `PromptResponse`; the v2 adapter streams it.
- **Execution environment:** the client execution environment (`ACPFileSystem` /
  `ACPExecutionEnvironment`) exists only in v1. A v2 session always runs tools on the host
  (or a configured environment) and reports terminals as display-only. The choice by client
  capabilities (`supports_client_execution` in `session.py`, with a local fallback) already
  covers v1 clients without fs or terminals, such as T3.

## Plan

1. **Routing seam (done in aclientp, unreleased).** `serve(agent, transport, *, v2_agent=...)`:
   with only the v1 agent, every transport builds the same `AgentSideConnection` as before;
   with a v2 agent, each connection is a `RoutedAgentConnection` (stdio, WebSocket, HTTP,
   streams). `RoutedAgentConnection` got `debug_file`, and `serve()`'s untyped `**kwargs`
   became `observers`. Tests: `aclientp/tests/v2/test_serve.py`. agentpool needs no change
   until it has a v2 agent to pass (step 3); its ACP tests pass on the new aclientp.
2. **Core/adapter split of `session.py` (done).** Protocol-neutral now:
   - `turns.py`: `TurnOutcome` (completed / cancelled / limit reached / failed / refused) and
     `TurnOutput` (`start`, `event`, `finish`). `ACPSession.run_turn` runs a turn (slash
     commands, then the agent) under the session's lock.
   - `output.py`: `SessionOutput`, i.e. everything a session sends (turn events, events
     between turns, changed mode/model/config options, the command list, errors, text,
     slash commands' tool calls).
   - `v1_output.py`: `V1SessionOutput` / `V1TurnOutput`, the v1 code that lived in the
     session. `process_prompt` maps the outcome to v1's stop reason.

   Still v1 and owned by the v1 adapter: `acp_agent.py` (request handlers),
   `input_provider.py` (permission requests and elicitation via `session.requests`),
   `from_acp_content` in `process_prompt`, the `supports_*` capability checks,
   `session.notifications` / `session.requests`, and `debug_commands.py` (test tooling
   for v1 notifications, which stays v1-only). For v2, `ACPSession` gets its output (and
   input provider) from the adapter instead of building `V1SessionOutput` in
   `__post_init__`.
3. **v2 adapter**, tested against the isolated T3 setup (a real v2 client) and with Zed
   as the v1 check; snapshot tests per version.
4. **Offer both by default**, with `serve-acp --max-protocol 1` as the escape hatch while
   the v2 schema is still an alpha.

## Notes

- aclientp is published from `~/dev/oss/aclientp`; agentpool pins it from PyPI
  (`aclientp[...]>=0.1.7`). Develop against an editable install
  (`uv pip install -e ../aclientp`, then `uv run --no-sync ...`), and release aclientp
  before agentpool depends on the new API.
- aclientp's v2 models check themselves against the pinned upstream schema:
  `python -m aclientp.schema_reference --protocol 2 check`.
