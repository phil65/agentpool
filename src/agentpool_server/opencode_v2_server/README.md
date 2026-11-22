# OpenCode V2 server

Serves an `AgentPool` through the [OpenCode](https://github.com/anomalyco/opencode) **V2** HTTP
API, so the OpenCode V2 TUI, web and desktop clients can drive pool agents.

```bash
agentpool serve-opencode [config.yml] --port 4096 [--password secret]
opencode --server http://127.0.0.1:4096          # OpenCode >= 2.x client
```

Auth is HTTP Basic with username `opencode` and the password from `--password` or
`OPENCODE_PASSWORD` (`OPENCODE_SERVER_PASSWORD` is accepted as well). Without a password, auth is
off.

## Protocol version

| | |
|---|---|
| Pinned OpenCode tag | **v2.3.2** (`opencode_v2_sdk/_version.py`) |
| Spec | `schema/opencode-v2/openapi.json` (copy of `packages/protocol/openapi.json` at that tag) |
| Models | `src/opencode_v2_sdk/models.py`, generated, **do not edit** |

The spec calls itself experimental and changes often: routes were renamed between v2.3.0 and
v2.3.2. Stay on a pinned tag and bump on purpose.

## Design

The server is **pool-wide**. Every pool node (agent or team) is an OpenCode agent. Each session
is bound to one node, and its agent and model can be switched at any time (V2 makes agent and
model session state).

Sessions are **event-sourced**, like in OpenCode:

```
HTTP route ──► V2Session.emit(EventClass, data)
                 ├─ durable event: seq++, applied to SessionProjection (messages, inbox)
                 │                 and SessionInfo, appended to the log (JSONL, EventStore)
                 └─ every event:   published on the EventBus (GET /api/event, SSE)
```

- `GET /api/session/{id}/message` is always derived from the durable log (`projector.py`, a port
  of OpenCode's `message-updater.ts`), so the live stream, the replayable log
  (`/api/experimental/session/{id}/log`) and the message list can't disagree.
- Prompts are admitted into the **inbox** (`session.inbox.enqueued`). The `SessionRunner` drains it
  and runs one execution per item: `execution.started` → `inbox.delivered` → steps →
  `execution.succeeded|failed|interrupted`. `delivery: "steer"` injects into a running turn
  (`agent.inject_prompt`), `"queue"` waits for the next one.
- `RunEventConverter` turns `run_stream` events into V2 step/text/reasoning/tool events. Each model
  request becomes one step (one assistant message). Tool calls are shown under OpenCode's tool
  names and argument names (`tool_display.py`), so the clients use their shell, read, edit, grep,
  … renderers. The original tool name is kept in the tool metadata (`agentpoolTool`). Tools follow
  the canonical lifecycle
  (`ToolCallStartEvent` → `ToolCallProgressEvent`* → `ToolCallCompleteEvent`) that every agent
  type emits.
- Event logs live in `DATA_DIR/opencode-v2/sessions/<id>.jsonl`. Sessions without a log (created
  through another protocol, or older ones) are rebuilt from AgentPool's stored conversation
  (`history.py`, which uses the same converter). Session summaries are also written to the pool's
  `SessionData` storage, so other protocols see V2 sessions.

## Modules

| Module | Role |
|---|---|
| `server.py` | `create_app()`, `OpenCodeV2Server(BaseServer)`, auth, CORS, error handlers |
| `state.py` | `ServerState`: session registry, loading and importing sessions, persistence, runners |
| `session.py` | `V2Session`: event-sourced session (emit, seq, persistence, log followers) |
| `projector.py` | Durable events → messages + inbox |
| `events.py` | Event builder, durable event union, ID helpers |
| `event_store.py` | JSONL event logs |
| `bus.py` | SSE fan-out for `/api/event` (slow subscribers are dropped) |
| `run_converter.py` | `RichAgentStreamEvent` → V2 session events |
| `runner.py` | Inbox draining, executions, interrupts, manual compaction, `AgentBinder` |
| `history.py` | Stored `ChatMessage`s → durable events |
| `catalog.py` | Pool nodes → `Agent.Info`; models, providers, `ModelRef` resolution |
| `interactions.py` | Permissions (tool confirmation, saved rules) and forms (elicitation) |
| `prompt_input.py` | Prompt admission (attachments, skills, agent mentions) |
| `tool_display.py` | Maps tool names/arguments onto OpenCode's renderers (`bash` → `shell`, …) |
| `session_ops.py` | Fork, revert, diffs, import, stats, instruction entries |
| `shells.py`, `ptys.py` | Background shell commands; PTYs with the OpenCode websocket protocol |
| `location.py` | Locations and projects (git root commit = project ID, else `global`) |
| `pagination.py`, `errors.py`, `deps.py` | Cursors, typed `{_tag, message}` errors, FastAPI deps |
| `routes/` | One module per API area. `misc.py` holds the unsupported ones |

## What is and isn't supported

All 173 operations of the pinned spec have a route (guarded by
`test_every_spec_operation_has_a_route`).

- **Implemented:** sessions (CRUD, agent/model switch, prompt, synthetic, interrupt, inbox,
  messages, context, durable log, fork, move, compact, revert, diff, generate, environment,
  instructions, stats, export/import), permissions + saved rules, forms, fs, vcs (git), worktrees,
  commands (slash commands), skills, MCP status/resources, shells, jobs (session shells and agent
  tasks such as subagent runs; see `tasks.py`), retries (`session.retry.scheduled`), usage limits
  (the main agent's plan windows as OpenCode Go meters; see `usage_limits.py`), PTYs.
- **Answered with empty lists / typed `ServiceUnavailableError` (503)**, because AgentPool has no
  counterpart: plugins and plugin RPC (`RpcError`), integrations, credentials, text/image/video/
  decision generation, voice, client MCP, MCP add/remove, persistent PTYs, media/blobs,
  automations, artifacts, references, web search, pairing, fs watch, heap dumps.
- Unknown routes answer `404` **without a JSON body**, like OpenCode. Clients use that to detect
  older servers.

## Known limitations

These come from the interim session/agent model. They are tracked, with the code to remove, in
`ARCHITECTURE_SESSIONS_AGENTS.md` §10.

- Pool agents are shared instances. `AgentBinder` serializes turns per agent and re-binds the
  instance to the session of each turn (`load_session` / `BaseAgent.start_new_session`).
- Native and Claude Code agents report steps (`StepStartedEvent` / `StepCompletedEvent`) with
  per-request usage. For Codex, ACP and AG-UI agents, step boundaries are inferred and the run's
  usage goes to the last step.
- Subagent activity is shown as progress of the delegating tool call, not as V2 child sessions.
- Fork and revert rewrite AgentPool storage only, not Claude Code's or Codex's native transcripts.
- Tool display mapping (`tool_display.py`) covers AgentPool's built-in tools and Claude Code's
  and Codex's common ones. Other tools use OpenCode's generic renderer.

## Upgrading to a new OpenCode version

1. Pick a `v2.x.y` tag. Don't use `releases/latest`, which can be the V1 line.
2. Set `OPENCODE_TAG` in `scripts/generate_opencode_v2_models.py` and run
   `uv run python scripts/generate_opencode_v2_models.py --download`.
3. Review `git diff schema/opencode-v2/openapi.json` (spec changes) and the model diff.
4. `uv run pytest tests/servers/opencode_v2_server`. The coverage test lists new routes. New or
   changed event and message types need `projector.py` / `run_converter.py` updates.
5. For behaviour (not just shapes), read the reference implementation at that tag:
   `packages/server/src/handlers/`, `packages/core/src/session/` (`message-updater.ts`,
   `projector.ts`, `inbox.ts`), and `packages/client/src/promise/generated/client.ts` for how
   clients call the API.

## Testing against a real client

- Get the client binary for the pinned tag:
  `gh release download v2.3.2 -R anomalyco/opencode -p opencode-linux-x64.tar.gz`.
- Non-interactive: `opencode run --server http://127.0.0.1:PORT "hello"`,
  `opencode session list --server …`, `opencode models --server …`.
- TUI: `opencode --server http://127.0.0.1:PORT`. To see which endpoints the TUI calls, run uvicorn
  with access logging (`log_level="info"`).
- In tests, use a pool with `model: test` agents and `storage: {providers: [{type: memory}]}`, and
  pass `create_app(..., store=EventStore(tmp_path))` so nothing goes to the real data directory
  (see `tests/servers/opencode_v2_server/conftest.py`).

## Splitting into a separate package

- `opencode_v2_sdk` depends only on pydantic and can move as is.
- The server depends on this slice of AgentPool:
  - Core types: `BaseAgent`, `AgentPool`, `Team` / `TeamRun` / `BaseTeam`, `MessageNode`,
    `ChatMessage`.
  - Events: `agentpool.agents.events` (stream event types) and `InputProvider`.
  - Storage and sessions: `StorageManager`, `SessionData`.
  - Pool services: `SkillsManager`, `compact_conversation`, `FileOpsTracker`, slash commands
    (`agentpool_commands.base.NodeCommand`).
  - Utilities: `agentpool.utils.{identifiers,time_utils,pydantic_ai_helpers,diffs}`,
    `agentpool_config.storage.DATA_DIR`, and `agentpool_server.base.BaseServer`.

  These are reasonable candidates for a stable public API. The CLI entry point
  (`agentpool_cli/serve_opencode.py`) would become a plugin command.
