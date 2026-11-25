# T3 Code integration

Status: 1 and 2 work (open items below), the rest are ideas. Written 2026-11-22 against T3 Code
`0.1.11-nightly.20261117` ([pingdotgg/t3code](https://github.com/pingdotgg/t3code)).

T3 Code runs coding agents (Claude Code, Codex, Cursor, Grok, OpenCode, Kiro, Antigravity, Pi,
Muse, any ACP agent) on one machine, called an *environment*, and gives web, desktop and mobile
clients one interface to them. Each environment is a Node server that owns projects, *threads*
(durable conversations) and *runs* (turns). It also handles worktrees, checkpoints and the PR
flow.

The Python client is in `~/dev/oss/pyt3client` (`pair()`, `T3Client`, typed models generated
for all ~50 MCP tools, plus the `pyt3` CLI). Its `CLAUDE.md` maps where each protocol detail
lives upstream. Read it before working on any of the items below.

## What T3 exposes

- **`/mcp`**: MCP over Streamable HTTP with OAuth 2.1. It's the supported route for outside
  agents and scripts, and what pyt3client uses. Headless sign-in uses a one-time pairing code
  (`t3 auth pairing create`). The token lasts 30 days and is approved at one access level
  (`read-only` … `full-access`).
- **Native RPC**: REST under `/api` plus Effect RPC over the WebSocket at `/ws`. The web,
  desktop and mobile clients use it, and it is the only surface with live streams
  (`orchestration.subscribeThread`). pyt3client doesn't implement it, and it needs its own
  pairing, because MCP tokens are rejected there.
- **Host-only `t3` CLI**: `t3 thread start/send/show/list`, `t3 project ...`. It reads the
  local data directory and cannot target a remote server. `pyt3` fills that gap.

The API models an agent, not just a toolset. A T3 thread maps onto `BaseAgent`:

| agentpool `BaseAgent` | T3 (`/mcp`) |
|---|---|
| session (`list_sessions` / `load_session` / `resume_session`) | thread (`t3_thread_list`, `t3_thread_read`) |
| `run` / `run_stream` | `t3_thread_launch` or `t3_thread_send` → `t3_thread_wait` → `t3_thread_read` |
| `steer()` / `queue_prompt()` | `t3_thread_send` `mode=steer` / `mode=queue` |
| `interrupt()` | `t3_thread_interrupt` |
| `get_available_models()` / `set_model()` | `orchestrator_capabilities` / `model_selection` (+ options such as effort) |
| `get_modes()` / `set_mode()` | `runtime_mode` (approval-required … full-access) × `interaction_mode` (default / plan) |
| approvals and questions (input provider) | `t3_pending_request` list / read / respond |
| subagents | `delegate_task` (only inside T3 threads); `t3_thread_launch` from outside |

The gap is streaming. `/mcp` has no token stream. Progress comes item by item, from polling
`t3_thread_read` with `afterPosition`: assistant messages, command runs, tool calls, and
reasoning in the `activity` view. Live streaming needs the native WebSocket.

## Opportunities, by value per effort

### 1. agentpool as a provider inside T3 (ACP): works

T3 can add any executable as a provider: **Settings → Providers → Add provider → Show more →
Local ACP command**. Pointing it at `agentpool serve-acp <config>` puts agentpool agents and
teams next to Claude and Codex in T3, with T3's worktrees, checkpoints, PR flow and mobile app.

T3 passes its own MCP server (`t3-code`, HTTP plus a bearer token scoped to the provider
session) in ACP `session/new`, `session/load` and `session/fork`. `ACPSession.initialize_mcp_servers()`
already connects session MCP servers, so agentpool agents get T3's orchestration tools
(`delegate_task`, `t3_thread_*`, `schedule_task`, …) without extra work.

Setup: add it in the app, or put this in `<T3 home>/userdata/settings.json`. Use absolute
paths, because T3 runs the executable directly, without a shell:

```json
{"providerInstances": {"agentpool": {
  "driver": "acpRegistry", "displayName": "agentpool",
  "config": {"source": "local",
             "commandPath": "/path/to/.venv/bin/agentpool",
             "commandArgs": ["serve-acp", "/path/to/config.yml"]}}}}
```

Tested 2026-11-22 with a `claude_code` agent (Haiku) against an isolated T3. The tests drove
T3 with pyt3client.

**Works**

- T3 registers the provider and can run child tasks on it. Its picker shows agentpool's
  models (`category: "model"` config option) and modes, plus effort for the current model.
  The model list fills in from a background probe a few seconds after startup; until then
  T3 shows a placeholder `default`.
- Turns run end to end: prompt → agentpool → Claude Code → reply, followed by a T3 checkpoint.
- T3's MCP server arrives in `session/new` and `session/load` as a *stdio* bridge
  (`t3 acp-mcp-bridge`, with endpoint and bearer in env). The agent calls T3 tools such as
  `t3-code.orchestrator_capabilities` with no agentpool changes.
- Resume after a T3 restart (`session/load`) keeps the conversation and the T3 tools.

**Fixed along the way**

- Resume was broken for `claude_code` and `codex`. ACP `session/load` passes the pool's
  session ID, but the agent resumed it as the CLI or Codex ID. `claude --resume ses_…`
  failed, and every later turn errored. For Claude, the pool's session row never even
  learned the CLI ID, because `ClaudeCodeAgent.storage` is Claude's own history
  (`ClaudeStorageProvider`), where `update_sdk_session_id` does nothing. Now
  `ClaudeCodeAgent` records the CLI ID on the pool's session
  (`_session_store` / `_record_sdk_session_id`), and both agents translate pool ID →
  CLI/thread ID in `load_session`. Tests: `tests/agents/claude_code_agent/test_slash_commands.py`,
  `tests/agents/codex_agent/test_load_session.py`.
- `MessageNode.storage` and the native agent's prompt manager tested `if self.agent_pool`.
  A pool is a registry, so it is falsy while empty. They now check `is not None`.

**Open**

- **Failed turns look successful to T3 over v1.** Over v2 they end with
  `stopReason: "error"`. `prompt()` reports agent failures as a notice
  and ends with `stopReason: end_turn` (deliberate, see its docstring). T3 then marks the
  run `completed`. ACP has no error stop reason, so a JSON-RPC error from `session/prompt`
  would make T3 mark the run `failed`. That would change behaviour for every ACP client
  (Zed, …), so it needs a decision.
- **ACP v2.** T3 offers `protocolVersion: 2`, and `serve-acp` answers both versions since
  2026-11-22 (`ACP_V2_SERVER.md`), so T3 gets v2: richer usage, plans, configuration and
  compaction messages, subagents as child sessions, cancellable tasks. Not yet checked end
  to end against T3.
- **Fidelity (fixed 2026-11-22):** empty reasoning items (signature-only thinking
  deltas), shell commands shown as `$ Tool` (the streamed call's final input never
  reached `rawInput`), and the rate limit notice repeated per request.
- **Sign-in.** agentpool advertises no ACP auth methods (TODO in
  `acp_agent.py authenticate`), so T3's **Sign in** button has nothing to offer. Agents
  still use their own logins.
- **Not yet checked:** permission requests in Supervised mode, checkpoint rollback (T3
  starts a fresh session and sends the kept conversation), native agents and teams as the
  default agent.

Upstream: `packages/provider-acp*`, `docs/user/providers-acp.md`,
`docs/orchestration-v2/orchestrator-mcp-server.md` (section "ACP Registry V2").

### 2. A `type: t3` agent in agentpool

One agent type brings in every provider T3 drives: Cursor, Grok, Kiro, Antigravity, Muse, Pi
and ACP Registry agents, all on the user's own subscriptions, on any machine T3 runs on
(homelab or VPS over Tailscale or T3 Connect). Worktree isolation and checkpoints come
for free. The agent joins teams, `|` / `&` composition and every agentpool server like any
other agent.

Built (2026-11-22): `src/agentpool/agents/t3_agent/`, config `src/agentpool/models/t3_agents.py`,
docs `docs/configuration/node-types/t3-agents.md`. Sign-in via a `pyt3 login` alias (`login`)
or `url` + `token`. An agentpool session is a T3 thread (launch, then send).
`_stream_events` polls `t3_thread_read` (`activity` view) from before the oldest unfinished
item, with paging; `items.ItemTracker` diffs items into events. Questions go through the input
provider (`questions.py`, `t3_pending_request` list/read/respond); approvals can't be given
over MCP, so a waiting approval gives one notice (`thread.pendingRequestCount > 0` with no
listed question). Models from `orchestrator_capabilities` as `provider/model`; config options
are runtime mode, interaction mode and model.

Tested live against the isolated T3 (claudeAgent / haiku): a command plus reply, a follow-up
over `t3_thread_send`, and a question answered through the input provider. Unit tests replay
the recorded MCP results (`tests/agents/t3_agent/data/two_turns.json`).

Findings:

- A run's status passes through `waiting` after the reply, before its checkpoint. It does
  not mean that the run waits for the user; `thread.pendingRequestCount` does.
- T3 adds a `command_execution` item "Preparing workspace" to the first run of a thread.
- An assistant message can appear only once complete, so text "streams" per message;
  reasoning grows in place.

Live updates (2026-11-22): with a native sign-in (`pyt3 login-native`, or
`native_token`), the agent also subscribes to the thread over T3's native WebSocket
(`pyt3client.native`, `t3_agent/live.py`). Growing reply and reasoning texts stream in
(T3 sends updates about 0.5 s apart; `/mcp` shows a reply only once complete), other
events trigger an MCP read (tool rendering, run status), and approval requests go to
the input provider and are answered with `runtime-request.respond`. Tested live:
approval of a command in `approval-required`, reasoning streamed in six steps.

Open: attachments (prompts must be text); agentpool toolsets are not passed to the
thread. (Questions are answered over the socket too since 2026-11-22: the socket's
`user_input_request` item carries them, and its request IDs are `/mcp`'s, so each is asked once.) `compact()` and `fork_session()` raise `UnsupportedOperationError` (T3 has
no API for either over MCP).

### 3. T3 as a toolset

This already works without code: add `https://<env>/mcp` to `mcp_servers` with an
`Authorization: Bearer <token>` header, using a token from `pyt3 login`. Any native agent then
gets the ~50 T3 tools. A curated toolset on pyt3client ("delegate to machine X": launch + wait
+ read, plus status) would give agents fewer, sharper tools. Item 2 covers delegation more
naturally, though, because a T3 agent can be a team member.

### 4. agentpool's OpenCode server as a T3 provider: untested

T3's OpenCode provider can connect to an external server by URL and password, and checks the
version (≥ 1.14.19, or 2.0.18 for OpenCode 2). This was blocked while `serve-opencode` was the
v1 server (it reported `0.1.0`). Since 2026-11-22 `serve-opencode` is the V2 server and reports
the pinned version (2.4.1, `opencode_v2_sdk/_version.py`), so the check should pass. Whether T3's adapter
(`packages/provider-opencode`) works against our V2 API is untested. Item 1 reaches the same
goal and works today.

### 5. A T3 storage provider: done (2026-11-24)

`storage: providers: [{type: t3}]` (`agentpool_storage/t3_provider`) reads an environment's
threads as sessions over `/mcp` (`t3_thread_list`, `t3_thread_read` with the `messages` view),
signed in like a T3 agent (`url` + `token`, or a `pyt3 login` sign-in). It lists threads
(newest first, one project or all), loads their user and assistant messages with timestamps,
and gives each thread's project folder as `cwd`. Read-only: T3 keeps its threads. Not used:
`statev2.sqlite` (an internal schema, unsafe while T3 runs). The thread helpers it shares with
the T3 agent are in `agentpool/agents/t3_agent/threads.py`. Like the Claude and Zed providers,
it serves session listing and loading; `agentpool history show` needs the SQL provider's
filtered queries. Tested against replayed `/mcp` results, not yet a live server.

## Testing notes

- Never use the user's live T3 (`~/.t3/userdata`). Run an isolated server:
  `t3 serve --base-dir <tmp>/t3home --port 39731 --host 127.0.0.1 --no-browser <tmp>/proj`
  and mint codes with `t3 auth pairing create --base-dir <tmp>/t3home --json`.
- Start anything that spawns Claude without `ANTHROPIC_API_KEY`, `CLAUDECODE` and `CLAUDE_*`.
  Otherwise it bills that API key instead of the subscription login:
  `env $(env | grep -oE '^(ANTHROPIC_[A-Z_]*|CLAUDECODE|CLAUDE_[A-Z_]*)' | sed 's/^/-u /') <cmd>`.
  Agent turns on the subscription cost nothing extra.
