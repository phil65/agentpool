# ACP `_meta` / `field_meta` Reference

## Overview

The Agent Client Protocol (ACP) includes a `_meta` field (called `field_meta` in agentpool's Python implementation) on all message types for extensibility. This field allows clients and agents to pass custom metadata that isn't part of the core protocol.

Typed definitions of the conventions agentpool knows about live in
aclientp's [`src/aclientp/schema/field_meta.py`](https://github.com/phil65/aclientp/blob/main/src/aclientp/schema/field_meta.py).

## Protocol Specification

From the [ACP Extensibility docs](https://agentclientprotocol.com/protocol/extensibility):

> All types in the protocol include a `_meta` field with type `{ [key: string]: unknown }` that implementations can use to attach custom information. This includes requests, responses, notifications, and even nested types like content blocks, tool calls, plan entries, and capability objects.

### Trace Context (W3C)

The protocol reserves certain root-level keys for [W3C trace context](https://www.w3.org/TR/trace-context/):

```python
field_meta = {
    "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    "tracestate": "congo=t61rcWkgMzE",
}
```

## claude-agent-acp

Everything below describes `@agentclientprotocol/claude-agent-acp` **v0.108.1**
(ACP v1 code path, `src/v1/`). The package was previously published as
`@zed-industries/claude-code-acp` and `@zed-industries/claude-agent-acp`; both
names are deprecated. Field names change between releases, so check the
adapter source when upgrading.

JetBrains AIR extensions (`_meta.jetbrains.air`) are documented separately in the
adapter's [`docs/air-extensions.md`](https://github.com/agentclientprotocol/claude-agent-acp/blob/main/docs/air-extensions.md)
and are not repeated here.

### Prefer standard fields

Several things that used to be legacy `_meta` keys now have a preferred form,
mostly standard ACP fields. The adapter still reads the legacy keys, but the
preferred form wins when both are sent:

| Purpose | Preferred | Legacy `_meta` key |
| --- | --- | --- |
| System prompt | `systemPrompt` (`append` / `override`) | `_meta.systemPrompt` |
| Extra workspace roots | `additionalDirectories` | `_meta.additionalRoots` |
| Disable built-in tools | `_meta.claudeCode.options.tools: []` | `_meta.disableBuiltInTools` |

### Client → Agent

#### `session/new` (also `session/load`, `session/resume`, `session/fork`)

```python
from aclientp.schema import NewSessionRequest, SystemPrompt, TextContentBlock

request = NewSessionRequest(
    cwd="/path/to/project",
    additional_directories=["/path/to/shared"],
    system_prompt=SystemPrompt(
        mode="append", content=[TextContentBlock(text="Always write tests.")]
    ),
    field_meta={
        "claudeCode": {
            # Forwarded to the Claude Agent SDK's `Options`
            "options": {"maxTurns": 50, "disallowedTools": ["WebFetch"]},
            # Also emit raw SDK messages as `_claude/sdkMessage` notifications
            "emitRawSDKMessages": [{"type": "result"}],
        },
    },
)
```

`_meta.claudeCode.options` is passed to the SDK's `Options`, with these exceptions:

- **Ignored** (managed by the adapter): `cwd`, `includePartialMessages`,
  `permissionMode`, `canUseTool`, `executable`.
- **Merged** with the adapter's own values: `hooks`, `mcpServers`, `disallowedTools`.
- **Passed through**: `tools` (defaults to the `claude_code` preset).
- `allowDangerouslySkipPermissions: false` removes `bypassPermissions` from the
  session (repeat it on `session/load`); `true` cannot override running as root.

`_meta.claudeCode.emitRawSDKMessages` is `true` (all messages), `false` (default),
or a list of filters `{type, subtype?, origin?}`; matching SDK messages are sent
as the `_claude/sdkMessage` extension notification.

#### `initialize` client capabilities

| Key | Effect |
| --- | --- |
| `clientCapabilities._meta.terminal_output` | Client renders terminals from `terminal_info` / `terminal_output` / `terminal_exit` tool-call meta |
| `clientCapabilities._meta.terminal_output_delta` | Like `terminal_output`, but output arrives as appended `terminal_output_delta` chunks |
| `clientCapabilities._meta["terminal-auth"]` | Offer terminal login methods (legacy form of `clientCapabilities.auth.terminal`) |
| `clientCapabilities._meta["subagent-transcript"]` | Client wants subagent transcripts |
| `clientCapabilities.auth._meta.gateway` | Offer the `gateway` auth method (custom Anthropic-protocol endpoint) |

#### `authenticate` with the `gateway` method

```json
{ "_meta": { "gateway": { "baseUrl": "https://gateway.example", "headers": { "X-Key": "…" } } } }
```

#### `_session/steering` (extension request)

Steers the running turn instead of queueing a new `session/prompt`. Supported
when `InitializeResponse._meta.steering.supported` is set. With
`_meta.steering.idleBehavior: "promptRequired"`, a call without a running turn
returns `{ "outcome": "promptRequired" }` instead of starting one.

### Agent → Client

#### Tool calls (`tool_call` / `tool_call_update`)

```typescript
{
  "claudeCode": {
    "toolName": "Edit",           // Claude Code tool name (also ACP `name` on `tool_call`)
    "toolResponse": { ... },      // structured tool output
    "parentToolUseId": "toolu_…", // set for tool calls made inside a subagent
    "nonExecutionKind": "user-rejected", // on `failed`: why the tool never ran
                                         // ("permission-rule", "interrupted", "cancelled", …)
    "userFeedback": "…",          // text the user gave when rejecting; only with nonExecutionKind
    "mcpServer": { "name": "…", "source": "…" } // for `mcp__*` tools, on permission requests
  },
  // Only to clients that declared `_meta.terminal_output(_delta)`:
  "terminal_info": { ... },
  "terminal_output": "…",        // or "terminal_output_delta"
  "terminal_exit": { "terminal_id": "…", "exit_code": 0, "signal": null }
}
```

#### Other notifications and responses

| Where | Key | Content |
| --- | --- | --- |
| `session_info_update` | `_meta.promptSuggestions` | `{ "suggestions": [{ "text": "…" }] }`: predicted next prompt |
| `usage_update` | `_meta["_claude/model"]` | Model of the last assistant message |
| `usage_update` | `_meta["_claude/rateLimit"]` | SDK rate-limit info |
| `usage_update` | `_meta["_claude/origin"]` | Origin of the turn's result message |
| `session/prompt` response | `_meta.quota` | `{ token_count, model_usage: [{ model, token_count }] }` |
| `InitializeResponse` | `_meta.steering` | `{ "supported": true }`: accepts `_session/steering` |
| `InitializeResponse.agentCapabilities` | `_meta.claudeCode.promptQueueing` | Prompts sent during a turn are queued |
| `InitializeResponse.agentCapabilities` | `_meta.authStatus` | Empty object: agent pushes `_auth/status_update` |
| Extension notification | `_claude/sdkMessage` | Raw SDK message (see `emitRawSDKMessages`) |

## agentpool

- **agentpool as an ACP client** (`type: acp` agents such as `claude-agent-acp`)
  sends no adapter-specific `_meta`: it uses the standard `systemPrompt` and
  `additionalDirectories` fields, and its `initialize` request advertises only
  standard capabilities. To control Claude Code options (model, tools,
  permissions, hooks), use the native `type: claude` agent, which drives the
  Claude Agent SDK directly instead of going through this adapter.
  When listing sessions of such an agent, it reads `_meta.created_at` from each
  listed session if the agent provides it.

## References

- [ACP Extensibility Documentation](https://agentclientprotocol.com/protocol/extensibility)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
- [claude-agent-acp Source](https://github.com/agentclientprotocol/claude-agent-acp)
- [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-typescript)
