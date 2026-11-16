# ACP `_meta` / `field_meta` Reference

## Overview

The Agent Client Protocol (ACP) includes a `_meta` field (called `field_meta` in agentpool's Python implementation) on all message types for extensibility. This field allows clients and agents to pass custom metadata that isn't part of the core protocol.

## Protocol Specification

From the [ACP Extensibility docs](https://agentclientprotocol.com/protocol/extensibility):

> All types in the protocol include a `_meta` field with type `{ [key: string]: unknown }` that implementations can use to attach custom information. This includes requests, responses, notifications, and even nested types like content blocks, tool calls, plan entries, and capability objects.

## Common Use Cases

### 1. Client → Agent (NewSessionRequest)

Clients like Zed can send configuration via `_meta` in the `newSession` request:

```python
from acp.schema import NewSessionRequest

session_request = NewSessionRequest(
    cwd="/path/to/project",
    mcp_servers=[...],
    field_meta={
        # Disable built-in tools to prefer custom implementations
        "disableBuiltInTools": False,
        # Custom system prompt
        "systemPrompt": {"append": "Always write tests for new functions."},
        # Agent-specific options (e.g., for claude-code-acp)
        "claudeCode": {
            "options": {
                "resume": "previous-session-id",
                "maxTurns": 50,
                "extraArgs": {"session-id": "custom-session-123"},
            }
        },
    },
)
```

### 2. Agent → Client (SessionNotifications)

Agents can include additional context in notifications:

```python
from acp.schema import SessionUpdate, ToolCallUpdate

# Tool call update with metadata
update = ToolCallUpdate(
    tool_call_id="call_123",
    status="completed",
    field_meta={
        "claudeCode": {
            "toolName": "Edit",  # Internal tool name
            "toolResponse": {  # Structured response
                "files_modified": ["file.py"],
                "lines_changed": 10,
            },
        }
    },
)
```

### 3. Trace Context (W3C)

The protocol reserves certain root-level keys for [W3C trace context](https://www.w3.org/TR/trace-context/):

```python
field_meta = {
    "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    "tracestate": "congo=t61rcWkgMzE",
}
```

## claude-code-acp Specific

The `@zed-industries/claude-code-acp` adapter recognizes these `_meta` fields:

### NewSessionRequest._meta

```typescript
{
  // Custom system prompt (string or object with 'append')
  "systemPrompt": "Custom prompt..." | { "append": "Additional text" },
  
  // Disable all Claude Code built-in tools (default: false)
  "disableBuiltInTools": boolean,
  
  // Claude Code SDK options
  "claudeCode": {
    "options": {
      // Resume previous session
      "resume": "session-id-to-resume",
      
      // Max conversation turns
      "maxTurns": number,
      
      // Additional CLI args for Claude Code SDK
      "extraArgs": {
        "session-id": "custom-id",
        "custom-flag": "value"
      },
      
      // MCP servers (merged with top-level mcp_servers)
      "mcpServers": {...},
      
      // Hooks for tool execution
      "hooks": {
        "PreToolUse": [...],
        "PostToolUse": [...]
      },
      
      // Abort controller for cancellation
      "abortController": AbortController
    }
  }
}
```

### ToolCallUpdate._meta

```typescript
{
  "claudeCode": {
    // The original Claude Code tool name (before MCP qualification)
    "toolName": "Edit" | "Read" | "Write" | "Bash" | ...,
    
    // Structured response from Claude Code tool
    "toolResponse": {
      // Tool-specific structured output
      // E.g., for Edit: { files_modified: [...], diff: "..." }
    }
  }
}
```

## Current Limitations in Agentpool

### ClaudeACPAgentConfig

The `ClaudeACPAgentConfig` class currently **does not** send `_meta` in newSession requests, which means:

❌ **Non-functional** (generates CLI args that are ignored):
- `builtin_tools`
- `allowed_tools` 
- `disallowed_tools`
- `permission_mode`
- `model`
- `add_dir`
- `fallback_model`
- `auto_approve`

✅ **Functional** (sent as top-level params):
- `cwd`
- `mcp_servers`

### Why CLI Args Don't Work

The `claude-code-acp` binary is a **pure stdin/stdout protocol adapter** that doesn't parse CLI arguments. All configuration must come through:

1. ACP protocol messages (`_meta` field)
2. Settings files (`.claude.json`, `.claude/settings.json`)
3. Environment variables

## Future Enhancement

To make `ClaudeACPAgentConfig` functional, implement `_meta` support:

```python
# In acp_agent.py
async def start(self) -> None:
    # ... existing code ...

    # Build _meta from config
    meta: dict[str, Any] = {}

    if isinstance(self.config, ClaudeACPAgentConfig):
        meta["disableBuiltInTools"] = False  # Or from config

        claude_options: dict[str, Any] = {}
        if self.config.builtin_tools is not None:
            claude_options["allowedTools"] = self.config.builtin_tools
        if self.config.model:
            # Note: This would need to be handled in claude-code-acp's extraArgs
            pass

        if claude_options:
            meta["claudeCode"] = {"options": claude_options}

    session_request = NewSessionRequest(
        cwd=cwd, mcp_servers=mcp_servers, field_meta=meta if meta else None
    )
```

## References

- [ACP Extensibility Documentation](https://agentclientprotocol.com/protocol/extensibility)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
- [claude-code-acp Source](https://github.com/zed-industries/claude-code-acp)
- [Claude Code SDK](https://github.com/anthropics/claude-code)
