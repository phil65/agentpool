---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool/models/t3_agents.py
title: T3 Code Agents
description: Run turns as threads in a T3 Code environment
icon: material/server-network
---

T3 agents hand their prompts to a thread in a [T3 Code](https://github.com/pingdotgg/t3code) environment. T3 Code drives coding agents (Claude Code, Codex, Cursor, Grok, OpenCode, Kiro, ACP agents, ...) on the machine it runs on, with projects, worktrees and checkpoints. The thread runs there, in T3's own provider sessions, so a flat-rate Claude or ChatGPT subscription set up in T3 Code is what pays for it.

The agent talks to the environment's MCP endpoint through [`pyt3client`](https://github.com/phil65/pyt3client).

## How it works

- One AgentPool session is one T3 thread. The first prompt launches it (`t3_thread_launch`), later prompts send to it (`t3_thread_send`).
- While a turn runs, the agent streams what changes in the thread: assistant text, reasoning, commands and other tool work as tool calls, and T3's notices.
- Questions of the thread's agent are asked through AgentPool's input provider (in Zed, OpenCode and similar clients as a form). With a native sign-in they are answered over the socket as soon as they appear; otherwise with `t3_pending_request` on the next read.
- **With a native sign-in** (see below), the agent also follows the thread over T3's native WebSocket: replies and reasoning stream in as they are written, and approvals (`runtime_mode: approval-required` or `auto-accept-edits`) are asked through the input provider, like other agents' tool confirmations.
- **Without one**, the agent reads the thread every `poll_interval` seconds over MCP, where a reply only shows once it is complete. T3's MCP endpoint cannot answer approvals: a turn that needs one waits until someone approves it in T3 Code, and the agent reports that once. The default `runtime_mode: auto` needs none.
- Loading a session continues its thread; `list_sessions` lists the project's threads.

## Signing in

Pair once with the `pyt3` CLI (from `pyt3client`), with a pairing code from T3 Code's settings:

```bash
pyt3 login https://t3.example.ts.net ABCD-1234 --name homelab
```

Then use the alias as `login`. Alternatively, give `url` and an MCP bearer `token` (`${VAR}` references are expanded).

For live updates and approvals, add a native sign-in. T3's native surface and its MCP endpoint each refuse the other's token, and it takes a pairing *token* rather than a pairing code:

```bash
t3 auth pairing create --scope orchestration:read --scope orchestration:operate
pyt3 login-native https://t3.example.ts.net <pairing-token>   # saved under the same alias
```

With `url` instead of `login`, put the native bearer in `native_token`. `native: true` makes a native sign-in required; `native: false` never uses it.

## Configuration Reference

/// mknodes
{{ "agentpool.models.t3_agents.T3AgentConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx") }}
///

## Basic Usage

```yaml
agents:
  claude_on_homelab:
    type: t3
    login: homelab
    project: /home/me/src/app   # the project's folder on the T3 host, or its id
    provider: claudeAgent
    model: claude-sonnet-5-5
    options:
      effort: high

  reviewer_in_worktree:
    type: t3
    url: https://t3.example.ts.net
    token: ${T3_TOKEN}
    project: proj_123
    provider: codex
    model: gpt-6.2-sol
    worktree: origin/main       # each new thread gets a new worktree from this ref
    interaction_mode: plan
```

## Configuration Notes

- Without `provider` and `model`, threads use the project's default model.
- `options` are model options as T3's `orchestrator_capabilities` lists them (e.g. `effort`).
- Models are listed and set as `provider/model` (e.g. `codex/gpt-6.2-sol`). Model, runtime mode and interaction mode apply to threads launched afterwards.
- Prompts must be text: T3 threads take text messages.
- AgentPool toolsets and MCP servers are not passed to the thread; its tools are those of the T3 provider.
- Read-only commands (e.g. `ls`) need no approval in T3, even with `approval-required`.
