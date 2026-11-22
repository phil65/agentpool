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
- While a turn runs, the agent reads the thread's items every `poll_interval` seconds and streams what changed: assistant text, reasoning, commands and other tool work as tool calls, and T3's notices.
- Questions of the thread's agent are asked through AgentPool's input provider (in Zed, OpenCode and similar clients as a form) and answered with `t3_pending_request`.
- Permission requests cannot be approved through T3's MCP endpoint. A turn that needs an approval waits until someone approves it in T3 Code, and the agent reports that once. Use a `runtime_mode` that needs no approvals (the default, `auto`) to avoid it.
- Loading a session continues its thread; `list_sessions` lists the project's threads.

## Signing in

Pair once with the `pyt3` CLI (from `pyt3client`), with a pairing code from T3 Code's settings:

```bash
pyt3 login https://t3.example.ts.net ABCD-1234 --name homelab
```

Then use the alias as `login`. Alternatively, give `url` and an MCP bearer `token` (`${VAR}` references are expanded).

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
