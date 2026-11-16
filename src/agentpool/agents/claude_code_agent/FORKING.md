# Session Forking in Claude Code Agent

## Overview

The Claude Code SDK supports **session forking** - creating a new session that shares the conversation history of a parent session but gets its own session ID. This enables ephemeral queries that don't pollute the main conversation history.

## How It Works

### Basic Fork (with parent still alive)

```python
# Parent session is running
parent_session_id = "abc-123"

# Create fork with same context
options = ClaudeAgentOptions(
    resume=parent_session_id,  # Which session to fork from
    fork_session=True,  # Create new ID instead of resuming same
)
fork_client = ClaudeSDKClient(options=options)
await fork_client.connect()

# Fork has parent's context but different session ID
await fork_client.query("What did we discuss?")  # Knows parent's history
# Get response...

await fork_client.disconnect()
# Parent session remains unchanged
```

### Key Properties

1. **Parent stays alive** - No need to disconnect/reconnect parent session
2. **Context preserved** - Fork has full conversation history up to fork point
3. **Isolated changes** - Changes in fork don't affect parent
4. **Unique session ID** - Each fork gets its own session identifier
5. **Multiple forks** - Can create multiple concurrent forks from same parent

## Use Cases

### Ephemeral Queries

Execute one-off queries without adding to main conversation:

```python
async def run(self, prompt, store_history=True):
    if store_history:
        # Normal - adds to history
        await self.main_client.query(prompt)
    else:
        # Ephemeral - fork, query, discard
        options = ClaudeAgentOptions(resume=self.session_id, fork_session=True)
        fork = ClaudeSDKClient(options=options)
        await fork.connect()
        result = await fork.query(prompt)
        await fork.disconnect()
        return result
```

### Subagent Spawning

Spawn a subagent that has access to current context:

```python
# Main agent has context: "The secret is XYZ"
options = ClaudeAgentOptions(resume=main_session_id, fork_session=True)
subagent = ClaudeSDKClient(options=options)
await subagent.connect()

# Subagent knows the secret
await subagent.query("What was the secret?")  # Knows "XYZ"
```

### A/B Testing

Try different approaches with same context:

```python
# Fork for approach A
fork_a = create_fork(session_id)
await fork_a.query("Solve using recursion")

# Fork for approach B
fork_b = create_fork(session_id)
await fork_b.query("Solve using iteration")

# Compare results, parent unchanged
```

## Implementation Notes

### Successful Fork Requirements

Both parameters are required for forking:
- `resume`: Must specify the session ID to fork from
- `fork_session`: Must be `True`

Without `resume`, `fork_session=True` creates a fresh session (no context).

### Session ID Retrieval

Get session ID from `ResultMessage`:

```python
async for msg in client.receive_messages():
    if isinstance(msg, ResultMessage):
        session_id = msg.session_id
        break
```

### Concurrent Forks

Multiple forks can run simultaneously:

```python
# All forks share parent context but have unique session IDs
fork1 = create_fork(parent_id)  # session: aaa-111
fork2 = create_fork(parent_id)  # session: bbb-222
fork3 = create_fork(parent_id)  # session: ccc-333
```

## Limitations

1. **Context is snapshot** - Fork gets parent's history at fork time, not live updates
2. **One-way isolation** - Fork changes don't affect parent, but parent changes don't reach fork
3. **Session persistence** - Parent session must exist (be saved/active) to fork from it

## Testing

See test files in project root:
- `test_fork_with_context.py` - Context preservation tests
- `test_resume_without_disconnect.py` - Concurrent forking tests
- `test_explicit_resume.py` - Explicit session resume tests

## Example: Complete Ephemeral Query

```python
from clawd_code_sdk import ClaudeSDKClient
from clawd_code_sdk.models import ClaudeAgentOptions, ResultMessage, AssistantMessage


class ClaudeCodeAgent:
    def __init__(self):
        self.main_client = None
        self.session_id = None

    async def connect(self):
        self.main_client = ClaudeSDKClient()
        await self.main_client.connect()
        # Capture session ID
        await self.main_client.query("Ready")
        async for msg in self.main_client.receive_messages():
            if isinstance(msg, ResultMessage):
                self.session_id = msg.session_id
                break

    async def ephemeral_query(self, prompt: str) -> str:
        """Execute query without affecting main conversation."""
        options = ClaudeAgentOptions(resume=self.session_id, fork_session=True)
        fork = ClaudeSDKClient(options=options)

        try:
            await fork.connect()
            await fork.query(prompt)

            result = ""
            async for msg in fork.receive_messages():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if hasattr(block, "text"):
                            result += block.text
                if isinstance(msg, ResultMessage):
                    break

            return result
        finally:
            await fork.disconnect()
```

## Related

- [Claude Agent SDK Documentation](https://docs.anthropic.com/en/api/claude-agent-sdk)
- ACP `session/fork` RFD (protocol-level forking)
