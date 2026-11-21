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
`initialize` `_meta`); Codex could use its native `turn/steer`.

## 5. "Always allow" permission choices

`ConfirmationResult` is allow / skip / abort. The CLI sends `permission_suggestions` (rules like
`Bash(git status:*)`, directories) with each request; Zed offers them as "Yes, and don't ask
again for ..." and returns them as `updatedPermissions` (`permissions/options/*.ts`,
`permissions/effects.ts`). Touches the agentpool-wide confirmation model and the servers.

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

## 9. Background tasks: stop and output

We report task events. Zed also stops tasks (`stop_task`), reads their output
(`get_task_output`), and polls the output file of a backgrounded Bash command
(`background-output.ts`).

## 10. `/mcp` command

We filter it out. Zed shows the server status and runs reconnect / enable / disable through
`mcp_reconnect` / `mcp_toggle`, since the CLI refuses them in SDK mode (`mcp-command.ts`).

## Done: 11. Session titles

Claude Code agents title their sessions through the CLI (`generate_session_title`, SDK 1.2.9)
instead of the storage's `title_generation_model` (which needs another API key): at the first
prompt, in the background; a prompt too short to title is its own title. Stored and announced
through `metadata_generated`, so all servers show it (`TITLES_OWN_SESSIONS`).

## 12. Idle compaction

Zed runs `/compact` about 54 minutes after the last turn when the context is over 200k tokens,
just before the 1h prompt cache expires (`idle-compaction.ts`). The CLI does this only for
interactive sessions.

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

## 17. Manifest `commands:` are never registered

`AgentsManifest.commands` (static text or callable commands in YAML) is parsed, but
`get_command_configs()` has had no callers since it was added ("prep command stuff",
2025-11-04): defined commands reach no agent or server. Wire them into each agent's command
store (then every server offers them), or remove the field.

## Done: 18. Backend commands run as turns everywhere

`BaseAgent.run_stream_with_commands()` (the OpenCode V2 runner's path) ran every `/word` text
through the agent's command store, so the CLI's prompt-like commands (`/review`, skills,
`/compact`) ran outside a run (permission requests failed) and unknown commands errored. It
now runs only the agent's own commands; backend commands (`is_backend_command`) and unknown
ones stay in the prompt, as in ACP. (MCP prompts already went through as turns: the `:` in
`mcp:server:prompt` never matched the command pattern.)
