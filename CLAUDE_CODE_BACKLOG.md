# Claude Code agent: backlog from comparing with Zed's adapter

Compared with [agentclientprotocol/claude-agent-acp](https://github.com/agentclientprotocol/claude-agent-acp)
(formerly `zed-industries/claude-code-acp`, TypeScript, v0.109, commit `184b7df`). File paths below
refer to that repo's `src/`. To be discussed one by one.

Also fixed along the way: Codex approvals the user rejected were still allowed (`5a39217f6`);
clawd-code-sdk 1.2.6–1.2.8 (flag settings, steering, lifecycle frames, CLI 2.1.330 fields).

Already done (first pass): plan mode no longer denies `ExitPlanMode`; our permission mode follows
`ExitPlanMode` / `EnterPlanMode`; failed results (`is_error`, `error_max_turns`, login required, ...)
end the turn with a `RunErrorEvent` and `finish_reason="error"`; tool results for unknown tool
calls no longer raise `KeyError`; `text/*` attachments become `<context>` text instead of raising.

## Done: 1–3 (live effort, models from the CLI, fast mode)

Needed clawd-code-sdk 1.2.6 (`apply_flag_settings` sent every default). Models come from the
CLI's init info; effort levels follow the selected model and change through flag settings
without reconnecting (an effort the new model lacks moves to the closest one it has); fast mode
is an `off`/`on` option for models with `supports_fast_mode`, and follows the
`fast_mode_state` of each result. Left open:

- **Thinking level option**: still offered (`set_max_thinking_tokens`). Every current model
  reports adaptive thinking and `supports_thinking_off: false`; Zed offers only effort. Drop it?
- **Context window**: still a guess (`[1m]` in the model ID: 1M, else 200k), see 7.
- **Fast mode unavailable**: the CLI reports why (`fast_mode_disabled_reason`: free plan, extra
  usage disabled, provider, ...); Zed puts it in the option's description.

## Done: 4. Steering

`BaseAgent.steer()` delivers user input into the running turn (default: `inject_prompt`).
Claude Code sends it with `client.steer()`. Priorities (probed on Opus): `next` (the default,
= OpenCode V2's "steer at the next Safe Step Boundary") joins the running cycle after the
current tool call; `now` (`steer(interrupt=True)`) ends the generation in progress; `later`
waits for the cycle to finish. A result of a cycle a steer was folded into names only the
steer, so a named result answers every message the CLI had taken in. The message joins the run's history at its
`command_lifecycle` `started` frame; `discarded` / `refused` become a warning notice. OpenCode V2
steering goes through it. Needs clawd-code-sdk with `steer()` and `CommandLifecycleMessage`.
Open: ACP has no steering method yet (Zed: `_session/steering` extension, advertised in
`initialize` `_meta`). Codex, OpenCode V2 and Antigravity now steer natively too (2026-11-22);
the ACP v2 server steers a prompt that arrives during a turn (`ACP_V2_SERVER.md`, Open).

## 5. "Always allow" permission choices

The decision model is in (2026-11-23, `agentpool/tools/decisions.py`): `Approve` / `Deny`, once
or for the session (the session keeps it, `SessionOverrides.tool_decisions`, for every agent
type), with edited arguments (`updated_input`) and a message to the model. Open: the CLI sends
`permission_suggestions` (rules like `Bash(git status:*)`, directories) with each request; Zed
offers them as "Yes, and don't ask again for ..." and returns them as `updatedPermissions`
(`permissions/options/*.ts`, `permissions/effects.ts`). That needs a rule-shaped scope beyond
"the tool, for the session", and servers that offer the suggested rules as options.

## 6. Plan approval choices

Approving a plan now returns to the mode from before planning. Zed offers "auto mode",
"accept edits", "bypass", and "clear context and ..." (restarts with only the plan,
`clear-context-coordinator.ts`). Needs 5's richer confirmation result.

## Done: 7. Context window

The CLI reports windows per concrete model ID (`claude-opus-5-5`, never `[1m]`); every current
model has 1M (the old 200k guess was wrong for all). Each result's `model_usage` teaches the
window of every model the turn used (also the CLI's own Haiku calls); `get_context_usage()`
(`raw_max_tokens`) fills in the current model at connect and after a model switch, without
overriding a result. Unknown models: 1M. Open: the category breakdown of `get_context_usage()`
could feed a "what fills my context" view.

## Done: 8. Bash results from the structured output

`to_bash_display()` builds output and exit code from the structured `BashOutput` (stdout and
stderr without the model's notes, the real code of a failure from `Exit code N`, no code for
interrupts, `returnCodeInterpretation` and backgrounded commands, a note for persisted large
output); ACP's display terminal and OpenCode's metadata use it. Also fixed: `tool_use_result`
comes as a single value for one tool result, so Edit/Read/Write metadata was never built.
Open: no `tool_progress` messages seen in probes (elapsed time of long tools).

## 9. Background tasks: stop and output (mostly done)

Done 2026-11-24: each session's runtime keeps the tasks its turns started until they finish
(also between turns), and agents list, stop and read them (`BaseAgent.running_tasks`,
`stop_task`, `task_output`; `/tasks`, `/tasks stop <id>`, `/tasks output <id>`). Claude Code
stops and reads them through the CLI process of their session (`stop_task`,
`get_task_output`); tasks agentpool runs itself (the subagent toolset's async mode, for every
agent type) are cancelled and read from the internal filesystem.

Open:
- `per_task_stop_affordance` is not declared, so an interrupt still stops the CLI's background
  agents too. Declaring it spares them (Zed's behaviour), but then only `/tasks stop` ends
  them: decide whether to declare it, and whether as an option.
- No protocol surface beyond `/tasks`: ACP v2's `tool_call/cancel` (a task's starting tool
  call) needs the v2 server off the v1 translation (`ACP_V2_SERVER.md`); OpenCode V2 only
  lists jobs.
- Polling a backgrounded Bash command's output file (Zed's `background-output.ts`).

## Done: 10. `/mcp` command

Done 2026-11-24. `/mcp` shows the agent's MCP servers and reconnects, enables or disables one
(`BaseAgent.reconnect_mcp_server` / `set_mcp_server_enabled`). Claude Code uses the SDK's
`mcp_reconnect` / `mcp_toggle` in every session's CLI process; native agents switch the
servers agentpool connects for them; Codex can only reload all of its servers; OpenCode V2
connects and disconnects. The V2 server serves `POST /api/experimental/mcp/{server}/connect`
and `/disconnect`. `/mcp` replaces `/list-mcp-servers`.

## Done: 11. Session titles

Claude Code agents title their sessions through the CLI (`generate_session_title`, SDK 1.2.9)
instead of the storage's `title_generation_model` (which needs another API key): at the first
prompt, in the background; a prompt too short to title is its own title. Stored and announced
through `metadata_generated`, so all servers show it (`TITLES_OWN_SESSIONS`).

## Done: 12. Idle compaction (Claude Code only)

`IdleCompaction` (port of Zed's rules) sends `/compact` ~54 minutes after the last request
when that request wrote the 1h cache, the context is 200k+ tokens
(`CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`), the rate limit is `allowed` and the agent is idle;
off via `idle_compaction: false`, the CLI's `autoCompactEnabled`/`idleCompaction` settings or
`DISABLE_COMPACT`/`DISABLE_AUTO_COMPACT`. Reported as an `auto` compaction in a background
turn. Not for Codex: its protocol has no cache TTL, it compacts by token limit itself, and
OpenAI's cache lifetime and pricing differ (unverified).

## Done: 13. Runs end at their answer, not at idle

Background Bash: the CLI goes idle after the user's result, fine. A background subagent keeps
the CLI `running` until it ends and the CLI's own follow-up cycle (`origin: task-notification`,
no `user_message_uuids`) is done: the run stayed open ~18s past its answer and took that
cycle's text, result and cost. Now the prompt goes out with a uuid and the run ends at the
result that names it (and every steered message); `idle` is the fallback. The rest is read
by the idle watcher. Open: a prompt sent while a background subagent still works waits for
the CLI to go idle (`IdleWatcher.stop`); the CLI itself would take it right away.

## Done: 14. MCP prompt commands

MCP prompts arrive after connect (`commands_changed`, `server:prompt (MCP)`); the agent now
follows the CLI's command list, and runs them in the CLI's spelling (clients see
`mcp:server:prompt`; a `/mcp:server:prompt` prompt is rewritten like Zed does). The ACP
server now lists the agent's commands too (and follows changes), runs agentpool commands of
the agent through its store (native `/compact`), and passes the backend's commands through as
prompts (`BaseAgent.is_backend_command`); a local command's reply (`/context`, which comes as
a complete, never streamed assistant message) now shows up in runs.

## 15. Revert (files and conversation) across agents

agentpool already reverts at the server level (OpenCode V2 `revert/stage` + `commit`, V1
`revert`): files from `pool.file_ops`, and the stored conversation. Gaps for external agents:
their built-in tools' edits are not in `pool.file_ops`, and their own state is not reverted
(the next turn still remembers the dropped turns). Backends: Claude Code rewinds files
(`rewind_files(user_message_id)`, needs `enable_file_checkpointing`) and resumes a session at a
message (`ResumeSession(at_message=...)`, a reconnect); Codex reverts its thread
(`thread/revert(before_turn_id)`), conversation only, no files. A shared
`BaseAgent.revert(before_message)` hook the servers call after their own revert could cover the
conversation for both; files stay agentpool's job (record the agents' edit results in
`pool.file_ops`). To design together with 5/6.

## Done: 16. `/clear` and `/cost` pass through

`/clear` works through the SDK and moves the CLI to a new session ID; the agent now adopts the
session ID of each result, so a reconnect does not resume the cleared conversation. agentpool's
own history keeps the earlier turns.

## 17. Prompt commands: templates send, actions act (design)

Manifest `commands:` are parsed but never registered (`get_command_configs()` has had no
callers since "prep command stuff", 2025-11-04). The underlying question is what a prompt-like
command does; agentpool has three behaviours today:

| Behaviour | Where | `/name args` |
|---|---|---|
| Stage | MCP prompts, prompt hub (ACP server) | expands, stages for the *next* message |
| Send as the turn | Claude Code CLI commands/skills, OpenCode V2 command route | expands, runs as the prompt now |
| Act | built-ins (`/clear`, `/model`), `callable` manifest commands | runs code, shows output |

Manifest `static`/`file` commands are templates but would have run as an "act" (printing the
expanded text). Elsewhere user-defined commands are templates sent as the turn (Claude Code
custom commands/skills, Codex custom prompts, OpenCode `command:` config, MCP prompts in Claude
Code); side-effect commands are built-ins; staging is unusual.

Proposal: decide by type. Templates (`static`, `file`, MCP prompts, prompt hub) expand and
send as the turn, expanded in one place before command splitting so every server behaves the
same; actions (`callable`, built-ins) run and show output, and call `queue_prompt()` to make
the agent do something; staging only as an explicit choice.

Open questions:
1. Is staging used deliberately (collecting prompts/resources before sending)? Keep it as an
   option (`/stage <template>` or a template flag), or drop it.
2. Should manifest templates get OpenCode-style `agent` / `model` routing?

## Done: 18. Backend commands run as turns everywhere

`BaseAgent.run_stream_with_commands()` (the OpenCode V2 runner's path) ran every `/word` text
through the agent's command store, so the CLI's prompt-like commands (`/review`, skills,
`/compact`) ran outside a run (permission requests failed) and unknown commands errored. It
now runs only the agent's own commands; backend commands (`is_backend_command`) and unknown
ones stay in the prompt, as in ACP. (MCP prompts already went through as turns: the `:` in
`mcp:server:prompt` never matched the command pattern.)

## Done: 19. MCP servers off the command line

clawd-code-sdk 1.3.0 sends MCP server configs in the initialize request
(`mcp_servers_delivery="initialize"`, CLI started with `--await-initialize`), so the toolset
bridge's and configured servers' `env` / `headers` no longer show in `ps`. Connecting fails if
the CLI doesn't report `mcp_servers_applied` (Claude Code < 2.1.261).

## Done: 20. `compact()` and `fork_session()`

Done 2026-11-24. `compact()` runs the CLI's `/compact` as an unstored turn of the session and
returns once it compacted (a failure is raised); `fork_session()` copies the CLI's session
(`clawd_code_sdk.fork_session`, fresh message UUIDs) and the fork's turns resume the copy.
Whole sessions only: our message IDs are not the CLI's. Both refuse before the session's first
turn. `native_compaction` and `native_fork` are on.
