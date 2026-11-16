# Claude Code SDK Command Event Streams

## `/compact` Command

Compacts conversation history into a summary to free up context window.

### Event Sequence

| Order | Event Type | Subtype | Key Data |
|-------|-----------|---------|----------|
| 1 | `InitSystemMessage` | `status` | `{'status': 'compacting'}` |
| 2 | `InitSystemMessage` | `status` | `{'status': None}` (cleared) |
| 3 | `InitSystemMessage` | `init` | Full session re-initialization |
| 4 | `InitSystemMessage` | `compact_boundary` | `{'compact_metadata': {'trigger': 'manual'|'auto', 'pre_tokens': int}}` |
| 5 | `UserMessage` | - | Full compacted summary text |
| 6 | `UserMessage` | - | `<local-command-stdout>Compacted </local-command-stdout>` |
| 7 | `ResultMessage` | `success` | Cost/usage info |

### Key Events

**`compact_boundary`** - Signals compaction occurred:
```python
InitSystemMessage(
    subtype="compact_boundary",
    data={
        "compact_metadata": {
            "trigger": "manual",  # 'manual' for /compact, 'auto' for automatic
            "pre_tokens": 15398,  # token count before compaction
        }
    },
)
```

**Summary `UserMessage`** - Contains the compacted conversation:
```
This session is being continued from a previous conversation that ran out of context.
The summary below covers the earlier portion of the conversation.
...
```

### Error Case (No Messages)

When there's nothing to compact:
```python
UserMessage(content="<local-command-stderr>Error: No messages to compact</local-command-stderr>")
```

---

## `/context` Command

Returns current context window usage statistics.

### Event Sequence

| Order | Event Type | Subtype | Key Data |
|-------|-----------|---------|----------|
| 1 | `InitSystemMessage` | `init` | Session initialization |
| 2 | `UserMessage` | - | Markdown-formatted context stats |
| 3 | `ResultMessage` | `success` | Completion info |

### Response Format

The `UserMessage` contains markdown in `<local-command-stdout>` tags:

```markdown
## Context Usage

**Model:** claude-opus-4-5-20251101  
**Tokens:** 19.1k / 200.0k (10%)

### Estimated usage by category

| Category | Tokens | Percentage |
|----------|--------|------------|
| System prompt | 3.0k | 1.5% |
| System tools | 16.1k | 8.0% |
| Messages | 8 | 0.0% |
| Free space | 135.9k | 68.0% |
| Autocompact buffer | 45.0k | 22.5% |
```

### Parsed Data

- **Model**: Current model name
- **Tokens**: Current usage / max capacity (percentage)
- **Categories**:
  - `System prompt` - Base system prompt tokens
  - `System tools` - Tool definitions
  - `Messages` - Conversation history
  - `Free space` - Available tokens
  - `Autocompact buffer` - Reserved for auto-compaction
