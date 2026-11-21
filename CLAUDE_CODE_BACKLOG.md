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

## 8. Bash results from the structured output

We pass the raw tool result text and `exit_code = 1 if is_error`. Zed uses `BashOutput`: stdout
and stderr without the model-only notes, `interrupted`, `returnCodeInterpretation` (a non-zero
exit that is not an error), and a note when the output was cut off and saved to
`persistedOutputPath` (`tool-calls/reporters/bash.ts`). `tool_progress` messages give the running
time of a call.

## 9. Background tasks: stop and output

We report task events. Zed also stops tasks (`stop_task`), reads their output
(`get_task_output`), and polls the output file of a backgrounded Bash command
(`background-output.ts`).

## 10. `/mcp` command

We filter it out. Zed shows the server status and runs reconnect / enable / disable through
`mcp_reconnect` / `mcp_toggle`, since the CLI refuses them in SDK mode (`mcp-command.ts`).

## 11. Session titles

We set `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, which also stops the CLI from titling
sessions. Zed asks with the `generate_session_title` control request (`session-titles.ts`); our
SDK does not wrap it yet.

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

## 14. Smaller prompt details

`/mcp:server:cmd args` is rewritten to `/server:cmd (MCP) args` (`prompt-to-claude.ts`).

## 15. File rewind

`enable_file_checkpointing` plus `rewind_files(user_message_id)` would undo a turn's edits.

## 16. Commands Zed filters that we offer

Zed's `UNSUPPORTED_COMMANDS` also has `clear` and `cost` (`slash-commands.ts`). We left `cost` in
on purpose; check what `/clear` does through the SDK (`conversation_reset`, our history and
session ID) before offering it.
