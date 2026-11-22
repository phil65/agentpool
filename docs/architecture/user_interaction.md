# User Interaction Architecture

## Core Abstraction

AgentPool uses **InputProvider** to handle user interactions across different execution contexts (CLI, ACP, OpenCode, tests).

### Three-Layer Architecture

```
┌─────────────────────────────────────────────┐
│ Layer 1: Tools (Protocol-Agnostic)         │
│ - question, tool confirmations              │
│ - Only knows MCP types                      │
│ - Calls ctx.handle_elicitation()            │
└─────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────┐
│ Layer 2: Context (Router)                   │
│ - get_input_provider()                      │
│ - Resolution: context → pool → fallback     │
│ - Pure delegation, no protocol logic        │
└─────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────┐
│ Layer 3: InputProvider (Protocol-Specific)  │
│ ┌──────┐ ┌─────┐ ┌──────────┐ ┌──────┐     │
│ │Stdlib│ │ ACP │ │ OpenCode │ │ Mock │     │
│ └──────┘ └─────┘ └──────────┘ └──────┘     │
└─────────────────────────────────────────────┘
```

**Why separate layers**: Different contexts have fundamentally different I/O mechanisms (blocking stdin vs SSE+HTTP vs protocol RPCs). Unifying them would violate their native patterns.

## Providers

### StdlibInputProvider
**Location**: `agentpool/ui/stdlib_provider.py`  
**Usage**: CLI, fallback  
**Mechanism**: Blocking `input()` calls  
**Limitations**: No async, no rich UI, no multi-select

### ACPInputProvider  
**Location**: `agentpool_server/acp_server/input_provider.py`  
**Usage**: ACP clients (Goose, Codex)  
**Mechanism**: Maps elicitation → `request_permission()` **[HACK]**  
**Why hacky**: ACP lacks native elicitation, shoehorns questions into permission system  
**Limitations**: Max 4 options, no multi-select, wrong semantics

### OpenCode V2 InputProvider
**Location**: `agentpool_server/opencode_v2_server/interactions.py`  
**Usage**: OpenCode V2 TUI/Desktop/Web  
**Mechanism**: SSE events (`permission.asked` / `form.created`) + HTTP reply endpoints  
**Flow**: Emit form/permission event → await HTTP reply → resolve future  
**Advantages**: Native forms & permissions, multi-select, unlimited options, rich descriptions

### MockInputProvider
**Location**: `agentpool/ui/mock_provider.py`  
**Usage**: Tests  
**Mechanism**: Pre-programmed responses

## OpenCode Flow (Detailed)

```
Tool: question("Which DB?", options=[...])
  ↓
Context: ctx.handle_elicitation(params)
  ↓
Provider: OpenCodeInputProvider.get_elicitation()
  │
  ├─ Generate question_id: "que_12345"
  ├─ Build OpenCode format with options
  ├─ Create asyncio.Future
  ├─ Store in state.pending_questions[id] = {future, ...}
  ├─ Broadcast SSE: QuestionAskedEvent
  └─ await future  # Blocks until HTTP response
  ↓
OpenCode UI receives SSE → shows question dialog
  ↓
User selects "PostgreSQL"
  ↓
POST /question/que_12345/reply {answers: [["PostgreSQL"]]}
  ↓
Route handler: provider.resolve_question(id, answers)
  ↓
future.set_result(["PostgreSQL"])
  ↓
Provider returns: ElicitResult(action="accept", content="PostgreSQL")
  ↓
Tool gets answer: "PostgreSQL"
```

**Key insight**: SSE broadcasts the question, HTTP receives the response. The future bridges the async gap.

## Provider Resolution

```python
context.input_provider          # 1. Explicit (servers set per-session)
  ↓ (if None)
context.pool._input_provider    # 2. Pool default
  ↓ (if None)
StdlibInputProvider()           # 3. Fallback
```

## Current Issues

### 1. Ownership Ambiguity
**Problem**: Can be set on agent, pool, or context with unclear precedence  
**Fix**: Context should **always** own it, resolve at creation time

### 2. Invisible to Observers
**Problem**: Input requests don't appear in event stream  
**Impact**: Can't observe when agent waits, can't replay conversations  
**Fix**: Emit `InputRequestEvent` and `InputResolvedEvent` while still using provider for response

### 3. ACP Elicitation Hack
**Problem**: Uses permissions for questions (semantic mismatch)  
**Options**: 
- Add elicitation to ACP spec
- Accept limitation and document clearly
- Use ACP resources for complex input

## Recommended Evolution

### Phase 1: Fix Ownership
```python
class NodeContext:
    input_provider: InputProvider  # Always set, never None
    
    @classmethod
    def create(cls, node, pool=None, input_provider=None):
        provider = input_provider or pool?._input_provider or StdlibInputProvider()
        return cls(node=node, input_provider=provider)
```

**Benefit**: Clear ownership, no scattered fallback logic

### Phase 2: Add Observability
```python
class InputProvider:
    event_emitter: EventEmitter | None
    
    async def get_elicitation(self, params):
        # Emit for observability
        if self.event_emitter:
            await self.event_emitter.emit(InputRequestEvent.from_params(params))
        
        # Handle via protocol-specific method
        result = await self._handle_elicitation(params)
        
        # Emit resolution
        if self.event_emitter:
            await self.event_emitter.emit(InputResolvedEvent(result))
        
        return result
```

**Benefit**: Input requests visible in stream, no breaking changes

### Phase 3: Bidirectional Streams (Future)
Support optional stream-based resume for advanced providers while keeping async fallback.

## Design Decision: Why Not Pure Event Stream?

**Considered**: Making all interactions part of the bidirectional event stream  
**Rejected because**:
- Different contexts are too different (blocking vs async vs protocol-specific)
- Adds bidirectional complexity to all clients
- Event stream becomes harder to reason about
- Current providers work well for their contexts

**Hybrid approach**: Emit events for observability, use providers for response handling

## Capability Matrix

| Feature | Stdlib | ACP | OpenCode | Mock |
|---------|--------|-----|----------|------|
| Text input | ✅ | ✅ | Future | ✅ |
| Tool confirm | ✅ | ✅ | ✅ | ✅ |
| Boolean | ✅ | ✅ | ✅ | ✅ |
| Single-select | ✅ | ✅ (≤3) | ✅ | ✅ |
| Multi-select | ❌ | ❌ | ✅ | ✅ |
| Descriptions | ❌ | ✅ | ✅ | ✅ |
| Free JSON | ✅ | ❌ | Future | ✅ |
| Async | ❌ | ✅ | ✅ | ✅ |

## Best Practices

**Tools**: Use MCP types, call `ctx.handle_elicitation()`, never check provider type  
**Servers**: Create provider per-session, inject via context  
**Tests**: Use MockProvider with pre-programmed responses  
**Agents**: Set at pool level unless run-specific override needed
