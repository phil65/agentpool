## Project Overview

AgentPool is a unified agent orchestration framework that enables YAML-based configuration of heterogeneous AI agents. It bridges multiple protocols (ACP, AG-UI, OpenCode, MCP) and supports native PydanticAI agents, Claude Code agents, Goose, and other external agents.

**Core Philosophy**: Define once in YAML, expose through multiple protocols, enable seamless inter-agent collaboration.

## Development Commands

### Installation & Setup
```bash
# Install with uv (recommended)
uv sync --all-extras

# Install specific extras
uv sync --extra coding --extra server
```

### Testing
```bash
# Run all tests (excludes slow and acp_snapshot by default)
uv run pytest

# Run with coverage
uv run pytest --cov-report=xml --cov=src/agentpool/ --cov-report=term-missing

# Run specific test markers
uv run pytest -m unit          # Unit tests only
uv run pytest -m integration   # Integration tests only
uv run pytest -m slow          # Include slow tests
uv run pytest -m acp_snapshot  # ACP snapshot tests

# Run single test file
uv run pytest tests/test_specific.py

# Run with verbose output
uv run pytest -vv

# Run tests in parallel
uv run pytest -n auto
```

### Code Quality
```bash

# Main command: runs all

duty lint

# Lint with ruff
uv run ruff check src/

# Format check
uv run ruff format --check

# Format code
uv run ruff format src/

# Type checking with ty
uv run ty check
```

### Running AgentPool
```bash
# Run agent directly
agentpool run <agent_name> "prompt text"

# Start ACP server (for IDEs like Zed)
agentpool serve-acp config.yml

# Start OpenCode server
agentpool serve-opencode config.yml

# Start MCP server
agentpool serve-mcp config.yml

# Start AG-UI server
agentpool serve-agui config.yml

# Start OpenAI-compatible API server
agentpool serve-api config.yml

# Watch for triggers
agentpool watch --config agents.yml

# View analytics
agentpool history stats --group-by model
```

## Code Architecture

### Module Structure

The codebase is organized into focused packages under `src/`:

- **`agentpool/`** - Core agent framework
  - `agents/` - Agent implementations (native, ACP, AG-UI, Claude Code)
  - `delegation/` - AgentPool orchestration, Team coordination, message routing
  - `messaging/` - Message processing, MessageNode abstraction, compaction
  - `tools/` - Tool framework and implementations
  - `tool_impls/` - Concrete tool implementations (bash, read, grep, etc.)
  - `models/` - Pydantic data models and configuration schemas
  - `prompts/` - Prompt management and templating
  - `storage/` - Interaction tracking and analytics
  - `mcp_server/` - MCP server integration
  - `running/` - Agent execution runtime
  - `sessions/` - Session management
  - `hooks/` - Event hooks system
  - `observability/` - Logging and telemetry (Logfire)

- **`agentpool_config/`** - Configuration models (separated for clean imports)
  - YAML schema definitions for agents, teams, tools, MCP servers

- **`agentpool_server/`** - Protocol servers
  - `acp_server/` - Agent Communication Protocol server
  - `opencode_server/` - OpenCode TUI/Desktop server
  - `agui_server/` - AG-UI protocol server
  - `openai_api_server/` - OpenAI-compatible API server
  - `mcp_server/` - Model Context Protocol server

- **`agentpool_toolsets/`** - Reusable toolset implementations
  - `builtin/` - Built-in toolsets (code, debug, subagent, file_edit, workers)
  - `mcp_discovery/` - MCP server discovery with semantic search
  - Specialized toolsets (composio, search, streaming, etc.)

- **`agentpool_storage/`** - Storage providers
  - `sql_provider/` - SQLAlchemy-based storage
  - `zed_provider/` - Zed IDE storage integration
  - `claude_provider/` - Claude storage integration
  - `opencode_provider/` - OpenCode storage integration

- **`agentpool_cli/`** - Command-line interface

- **`agentpool_commands/`** - Command implementations

- **`agentpool_prompts/`** - Prompt templates

- **`acp/`** - Agent Communication Protocol implementation
  - `client/` - ACP client implementations
  - `agent/` - Agent-side protocol implementation
  - `schema/` - Protocol schemas and types
  - `bridge/` - ACP bridge for connecting agents
  - `transports/` - Transport layer (stdio, websocket)

### Key Architectural Patterns

#### MessageNode Abstraction
All processing units (Agents, Teams) inherit from `MessageNode[TInputType, TOutput]`. This provides:
- Unified interface for message processing via `process()`
- Connection management (forwarding outputs between nodes)
- Hook system for intercepting messages
- Type-safe input/output handling

```python
# Both agents and teams are MessageNodes
agent: MessageNode[ChatMessage, ChatMessage]
team: MessageNode[ChatMessage, TeamRun]

# Nodes can be connected
agent.add_connection(other_agent)  # Forward messages to other_agent
```

#### AgentPool as Registry
`AgentPool` is a `BaseRegistry[NodeName, MessageNode]` that:
- Manages lifecycle of all agents and teams
- Provides dependency injection (shared_deps)
- Handles connection setup from YAML config
- Coordinates resource cleanup

#### Team Patterns
Teams combine multiple agents:
- **Sequential (chain)**: `agent1 | agent2 | agent3` - Output flows through pipeline
- **Parallel**: `agent1 & agent2 & agent3` - All process same input concurrently
- **YAML configuration**: Define teams in manifest with mode and members

#### Tool System
Tools follow PydanticAI's tool pattern with AgentPool extensions:
- Tools are typed functions with Pydantic schemas
- Can access `AgentContext` for agent-specific state
- Support `subagent` tool for delegation
- Built-in toolsets provide common functionality (code editing, bash, grep)

#### Protocol Bridging
AgentPool acts as a protocol adapter:
1. Agent defined once in YAML (with type: native/acp/agui/claude)
2. Pool loads and manages agent lifecycle
3. Server exposes agent through chosen protocol (ACP/AG-UI/OpenCode/OpenAI API)
4. Client interacts via standardized protocol

### Agent Types

**Native Agents** (`type: native`)
- PydanticAI-based agents with full framework features
- Direct model integration (OpenAI, Anthropic, Google, Mistral, etc.)
- Tool support, structured output, streaming
- Most flexible and feature-rich

**ACP Agents** (`type: acp`)
- External agents implementing Agent Communication Protocol
- Examples: Goose, Codex, custom ACP servers
- Communicate via stdio or websocket

**Claude Code Agents** (`type: claude`)
- Direct integration with Claude Code CLI
- Specialized for code-related tasks
- Access to Claude Code's tool ecosystem

**AG-UI Agents** (`type: agui`)
- Remote agents implementing AG-UI protocol
- HTTP-based communication
- Useful for distributed agent architectures

**File Agents** (`type: file`)
- Agent behavior defined by file content/prompts
- Lightweight for simple use cases

### Storage and Observability

**Storage Providers**: Track all agent interactions
- SQL-based with SQLModel/SQLAlchemy
- Per-agent or shared database
- Analytics via CLI: `agentpool history stats`

**Observability**: Logfire integration
- Structured logging with context
- Trace agent execution
- Performance monitoring
- Disabled in tests via env vars

### Configuration System

**YAML-First Design**:
- `AgentsManifest` is the root config model
- Supports inheritance via `INHERIT` field
- Inline schema definitions with Schemez
- Environment variable substitution
- Jinja2 templating in prompts

**Key Config Sections**:
- `agents`: Agent definitions
- `teams`: Multi-agent teams
- `responses`: Structured output schemas
- `mcp_servers`: MCP server configurations
- `storage`: Interaction tracking config
- `observability`: Logging/telemetry config
- `workers`: Background worker definitions
- `jobs`: Scheduled tasks

## Development Guidelines

### Code Style
- Python 3.13+ required (use modern syntax: match/case, walrus operator)
- Follow PEP 8 via Ruff
- Google-style docstrings (no types in Args section)
- Type hints required (type-checked with ty; complete annotations enforced by ruff's ANN rules)
- Use `from __future__ import annotations` for forward references

### Testing
- Tests use pytest (not in classes)
- Fixtures in `tests/conftest.py`
- TestModel from pydantic-ai for agent testing
- Disable observability in tests (see conftest.py)
- Markers: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.slow`

### Import Patterns
```python
# Avoid circular imports - use TYPE_CHECKING
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentpool.delegation import AgentPool

# Config models are in agentpool_config to avoid circular deps
from agentpool_config.teams import TeamConfig
```

### Tool Implementation
When adding new tools:
1. Create implementation in `agentpool/tool_impls/<tool_name>/`
2. Define config model in `agentpool_config/` if complex
3. Register in appropriate toolset (`agentpool_toolsets/`)
4. Add tests in `tests/tool_impls/`

### Adding Agent Types
New agent types require:
1. Config model in `agentpool/models/` (inherit from base, set `type` discriminator)
2. Implementation in `agentpool/agents/`
3. Add to `AnyAgentConfig` union in `manifest.py`
4. Update manifest loading in `pool.py`

### Server Implementation
New protocol servers:
1. Inherit from `BaseServer` in `agentpool_server/base.py`
2. Implement protocol-specific handlers
3. Use `AggregatingServer` if wrapping multiple agents
4. Add CLI command in `agentpool_cli/`

## Common Patterns

### Creating an AgentPool
```python
async with AgentPool("config.yml") as pool:
    agent = pool.get_agent("agent_name")
    result = await agent.run("prompt")
```

### Running Tests on Modified Code
```bash
# Find relevant tests
pytest tests/path/to/test_file.py -k "test_pattern"

# Quick sanity check (unit tests only)
pytest -m unit --no-cov

# Full validation
pytest && ty check && ruff check src/
```

### Debugging Agent Issues
1. Enable verbose logging (set `OBSERVABILITY_ENABLED=true`)
2. Check storage database for interaction history
3. Use `TestModel` for isolated testing
4. Add `--log-cli-level=DEBUG` to pytest

### Working with YAML Configs
- Examples in `site/examples/*/config.yml`
- Schema reference auto-generated from Pydantic models
- Validate with: `python -m agentpool_config.manifest config.yml`

## Special Considerations

### Async Context Managers
AgentPool and Agents use async context managers - always use `async with`:
```python
async with AgentPool(manifest) as pool:
    async with pool.get_agent("name") as agent:
        result = await agent.run("prompt")
```

### MCP Server Lifecycle
MCP servers are spawned as subprocesses - pool cleanup handles termination.
Use `ProcessManager` from `anyenv` for external process management.

### UPath for File Operations
Use `UPath` (universal_pathlib) not `Path` - supports remote filesystems (s3://, gs://, etc.)

### Model Configuration
Prefer string shorthand in YAML: `model: "openai:gpt-4o"`
Fallback models: `type: fallback, models: [primary, backup]`

### Entry Points
The project uses entry points for extensibility:
- `agentpool_toolsets` - Register custom toolsets
- `fsspec.specs` - Filesystem implementations (ACP)
- `universal_pathlib.implementations` - Path implementations

## Key Files to Understand

- `src/agentpool/delegation/pool.py` - AgentPool orchestration
- `src/agentpool/agents/agent.py` - Native agent implementation
- `src/agentpool/messaging/messagenode.py` - Base abstraction
- `src/agentpool/models/manifest.py` - Configuration schema
- `src/agentpool/tools/tool.py` - Tool framework
- `src/agentpool_server/acp_server/acp_agent.py` - ACP server agent wrapper
- `src/acp/client/protocol.py` - ACP client interface
- `src/acp/agent/protocol.py` - ACP agent interface

## Documentation

- Main docs: [phil65.github.io/agentpool](https://phil65.github.io/agentpool/)
- Built with MkNodes (see `mkdocs.yml`)
- Auto-generated from docstrings and examples. Utilities in agentpool/docs/

## Complete Usage Examples

### Direct Agent Instantiation

**Native Agent**
```python
from agentpool.agents import Agent


def greet(name: str) -> str:
    """Greet someone."""
    return f"Hello, {name}!"


async with Agent(
    name="my_agent",
    model="openai:gpt-4o-mini",  # Required: model string or Model instance
    system_prompt="You are a helpful assistant",
    tools=[greet],  # Callables or import paths like "mymodule:my_tool"
) as agent:
    async for event in agent.run_stream("Greet Alice"):
        ...
```

**ACP Agent**
```python
from agentpool.agents.acp_agent import ACPAgent

async with ACPAgent(
    command="goose",  # Required: executable name
    args=["acp"],  # Required: command arguments
    name="goose_agent",
    cwd="/path/to/project",
) as agent:
    async for event in agent.run_stream("Write code"):
        ...
```

**Claude Code Agent**
```python
from agentpool.agents.claude_code_agent import ClaudeCodeAgent

async with ClaudeCodeAgent(
    name="claude_coder",
    model="claude-sonnet-4-20250514",  # Optional: defaults to latest
    cwd="/path/to/project",  # Optional: defaults to current directory
) as agent:
    async for event in agent.run_stream("Refactor this code"):
        ...
```

### Agent from Config with Streaming

**Config (config.yml)**
```yaml
agents:
  coder:
    type: native
    model: "openai:gpt-4o-mini"
    system_prompt: "You are an expert Python developer"
    tools:
      - name: bash
        enabled: true
      - name: read
        enabled: true
```

**Python Code**
```python
from agentpool.delegation import AgentPool
from agentpool.agents.events import (
    PartDeltaEvent,
    ToolCallStartEvent,
    ToolCallCompleteEvent,
    StreamCompleteEvent,
)

async with AgentPool("config.yml") as pool:
    agent = pool.get_agent("coder")
    
    # Stream events (run_stream returns AsyncIterator, not a context manager)
    async for event in agent.run_stream("Read setup.py and list dependencies"):
        match event:
            case PartDeltaEvent(delta=text):
                # Stream text chunks as they arrive
                print(text, end="", flush=True)
            
            case ToolCallStartEvent(tool_name=name):
                print(f"\n[Tool starting: {name}]")
            
            case ToolCallCompleteEvent(tool_name=name, tool_result=result):
                print(f"\n[Tool {name} completed: {result}]")
            
            case StreamCompleteEvent(message=msg):
                # Final complete message with full content
                print(f"\n\nComplete response: {msg.content}")
```

Rules:
- ALWAYS use uv for all python related tasks.
- DO NOT USE getattr and hasattr in very rare exceptions. Always provide full type safety.
- Maximum type safety.
- never resort to shortcuts, never leave out stuff with TODOs unless explicitely asked.
