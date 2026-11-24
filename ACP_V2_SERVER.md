# ACP v2 for the agentpool ACP server

Status: done, `serve-acp` serves v1 and v2 (steps 1-4); open points below. Written
2026-11-22 against aclientp 0.1.7/0.1.8, which pin the v2 schema at `schema-v2.0.0-alpha.17`.

## Goal

`agentpool serve-acp` answers ACP v1 and v2 clients from one process. T3 Code asks for v2
(and falls back to v1, see `T3_CODE_INTEGRATION.md`). Zed asks for v1 by default: its v2
client (`crates/agent_servers/src/acp/v2.rs`, ACP SDK 5.4.0 with `unstable_protocol_v2`, on
Zed's main branch as of 2026-11-20) runs only with the `acp-beta` feature flag. That flag is
on for Zed staff and can be set by anyone (`"feature_flags": { "acp-beta": "on" }` in Zed's
settings); Zed then tries v2 and falls back to v1 if the agent refuses. v2 is close to stable,
and more clients are moving to it.

## Decision: one server, two protocol adapters

Not a second server or CLI command. ACP picks the version per connection, in `initialize`:
the client sends the highest version it supports, and the agent chooses. Whoever configures
the command (`agentpool serve-acp config.yml`) cannot know which version a client will ask
for, so one binary must answer both. aclientp already has the router for this,
`aclientp.router.RoutedAgentConnection` (it mirrors the TypeScript SDK's
`AgentProtocolRouter`). It picks the v1 or the v2 agent on `initialize` and never re-routes.

The OpenCode split (`serve-opencode` / `serve-opencode-v2`, until the v1 server was removed)
was no precedent: those were two unrelated REST APIs for different clients, with nothing to
negotiate. ACP v2 keeps v1's
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
3. **v2 adapter (done).** v2 is served through the v1 handler rather than beside it:
   - `v2_translate.py`: the event converter's v1 updates in v2 (chunks get `messageId`,
     tool calls become one upsert whose omitted fields patch nothing, edits become diffs
     with a git patch, config options, commands, plans). v1 location line 0 ("no line")
     is left out, since v2 lines start at 1.
   - `v2_client.py`: `V2ClientAdapter`, the v1 `Client` of a v2 session (translated
     updates, permission requests with title and subject, elicitation; no fs/terminal).
   - `v2_agent.py`: `AgentPoolV2Agent` translates each v2 request into the v1 one for an
     inner `AgentPoolACPAgent`. The prompt is v2's own: `session/prompt` returns the
     message id, and the turn runs via `run_turn` with `state_update` running → idle,
     including `stopReason: "error"` with an error object. `session/resume` with
     `replayFrom` replays (v1 load) and sends `replay_complete`.
   - `v2_output.py`: mode changes become config option updates.

   Wire-level tests: `tests/servers/acp_server/test_v2_agent.py` (a real v2 client
   connection over in-memory streams) and `test_v2_translate.py`. Live against T3 Code
   (2026-11-22): it negotiates v2, models and modes show, turns and T3 MCP tool calls run,
   resume after a T3 restart replays and keeps the conversation.
4. **Offer both by default (done):** `ACPServer(max_protocol=2)` passes
   `AgentPoolV2Agent` as `serve(v2_agent=...)`; `serve-acp --max-protocol 1` serves v1
   only. Requires aclientp >= 0.1.8.

## Open

- **v2 over v1, for now.** v2 sessions reuse the v1 output, translated
  (`v2_translate.py`); what v1 has no form for, the v2 output sends itself next to it
  (`V2SessionOutput.send`, from the agent's own events): agent-owned terminals
  (`v2_terminals.py`), the `cancelled` tool call status, `requires_action` states, and
  `tool_call/cancel` for tool calls whose task can be stopped (`v2_tasks.py`: a task
  marked `stoppable`, cancelled through `BaseAgent.stop_task`; the capability is withdrawn
  when the task ends), and subagent runs as child sessions (`v2_subagents.py`, since
  2026-11-24): a run's first event announces a child (`subagent_update` on its parent:
  title, state, `cancel` when its task can be stopped), the tool call that started it
  shows it (`session` content), and its events are the child's updates under the child's
  ID (own converter and translator). The child's state is mirrored on the association; it
  ends idle (`end_turn` / `error` / `cancelled`, with its run or task, or cancelled with
  the parent's turn when in the foreground), else `unknown`. Permission requests and
  elicitations of a child's tool calls carry the child's ID. A child takes only
  `session/cancel` (stops its task); other methods are refused with `-32602`. Not done:
  listing background children in `background`, child `usage_update`, replaying children
  on resume. Still missing otherwise: cancelling a tool call that started no task.

  Flip it (v2 as the core, v1 adapted down): nothing outside our code blocks it any more.
  v1 stays fully supported as an adapter, as Zed itself does it (its threads run on v2 types,
  v1 connections adapted up). Zed's users still connect with v1 by default (v2 only with
  `acp-beta`, staff by default; checked 2026-11-24), so the v1 adapter must stay complete.
  v2 clients to test with: acptui (v2 only, handles `subagent_update`), T3 Code (asks for
  v2), Zed with `acp-beta`. Already in place: one tool display model and stable user
  message IDs (`ARCHITECTURE_SESSIONS_AGENTS.md` §10.4, §11); still open: stable IDs per
  model response and a neutral transcript.
- **A prompt during a running turn is steered into it** (since 2026-11-22; before, it
  queued as the next turn). Text prompts steer, multimodal ones queue. Not configurable
  yet; decide whether it should be (a follow-up then lands in the current answer).
- ACP sessions on one shared agent run on their own pool `Session` and runtime,
  concurrently, for agents with `capabilities.concurrent_sessions`: native agents, T3 (a
  thread per session), Codex (a thread per session), ACP agents (an ACP session per session)
  and Claude Code (a CLI process per session). Cancel and steer reach only their own turn
  (`agentpool/agents/runtime.py`); modes and config options are per session
  (`ACPSession.set_mode` / `config_options`), and so are config change notifications. The
  others (AG-UI, OpenCode V2, Antigravity) run one turn at a time
  (`BaseAgent.session_turn()`).
- Each ACP session's turns run in its client's environment and with its instructions
  (working directory, the client's system prompt): `SessionEnvironment`, current while the
  session acts, instead of being set on the shared agents. Native agents, Claude Code and
  Codex take the instructions themselves (`capabilities.session_instructions`); for the
  others the client's prompt is staged into the first prompt. Rules and slash-command
  staging are per ACP session.

## Notes

- aclientp is published from `~/dev/oss/aclientp`; agentpool pins it from PyPI
  (`aclientp[...]>=0.1.8`). Develop against an editable install
  (`uv pip install -e ../aclientp`, then `uv run --no-sync ...`), and release aclientp
  before agentpool depends on the new API. As of 2026-11-24: aclientp 0.5.1, its models
  conform to the newest upstream schemas (v1 `schema-v1.35.0`, v2 `schema-v2.0.0-beta.4`,
  `python -m aclientp.schema_reference --protocol N check` / `latest`).
- aclientp's v2 models check themselves against the pinned upstream schema:
  `python -m aclientp.schema_reference --protocol 2 check`.
