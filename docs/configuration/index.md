---
title: Agent Manifest
description: Complete manifest structure and organization
icon: material/file-code
order: 2
---

The agent manifest is a YAML file that defines your complete agent setup.
The configuration is powered by [Pydantic](https://docs.pydantic.dev/latest/) and provides excellent validation
and IDE support for YAML linters by providing an extensive, detailed schema.

## Top-Level Structure

Here's the complete manifest structure with all available top-level sections:

/// mknodes
{{ "agentpool.AgentsManifest" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False) }}
///

## Top-Level Sections

/// mknodes
{{ "agentpool.AgentsManifest" | config_fields_table }}
///

| Section | Documentation |
|---|---|
| `agents` | [Node Types](node-types/index.md): native, Claude Code, Codex, ACP, AG-UI, OpenCode V2, Antigravity and T3 agents |
| `file_agents` | [File Agents](node-types/file-agents.md) |
| `teams` | [Teams](node-types/team.md) |
| `default_agent` | The agent clients talk to first (else the first agent) |
| `model_variants` | [Model Variants](model.md#model-variants) |
| `responses` | [Response Types](responses.md) |
| `mcp_servers` | [MCP Servers](mcp.md) |
| `pool_server` | [MCP Server](../servers/mcp-server.md) (`serve-mcp`) |
| `resources` | [Resources](resources.md) |
| `storage` | [Storage](storage.md) |
| `observability` | [Observability](observability.md) |
| `conversion` | [Document Conversion](../advanced/document-conversion.md) |
| `prompts` | [System Prompts](prompts.md), [Prompt Hubs](prompt-hubs.md) |
| `commands` | [Commands](commands.md) |
| `jobs` | [Tasks](tasks.md) |
| `compaction` | [Message History](../core-concepts/message-history.md#compaction) |
| `INHERIT` | [Inheritance](inheritance.md) |

## Configuration Resolution

AgentPool uses a layered configuration system inspired by OpenCode. Multiple config files are automatically discovered and deep-merged, allowing you to set global preferences while overriding project-specific settings.

### Precedence Order

Configs are loaded and merged in this order (later sources override earlier ones):

| Priority | Source | Location | Description |
|----------|--------|----------|-------------|
| 1 | Global | `~/.config/agentpool/agentpool.yml` | User-wide preferences |
| 2 | Custom | `AGENTPOOL_CONFIG` env var | CI/deployment overrides |
| 3 | Inline | `AGENTPOOL_CONFIG_CONTENT` env var | Runtime config as YAML/JSON string |
| 4 | Project | `agentpool.yml` in project root | Project-specific settings |
| 5 | Explicit | CLI argument | Highest precedence |
| fallback | Built-in | Package defaults | Only if no agents defined elsewhere |

!!! info "Deep Merge Behavior"
    Configs are **deep-merged**, not replaced. This means:
    
    - Nested dictionaries are merged recursively
    - Conflicting keys are overridden by higher-precedence sources
    - Non-conflicting settings from all sources are preserved
    - Lists are replaced entirely (not concatenated)

### Project Config Discovery

Project config is discovered by searching for `agentpool.yml` (or `.yaml`, `.json`, `.jsonc`) starting from the current directory and traversing up to the nearest git repository root.

### Fallback Behavior

The built-in fallback config (e.g., `acp_assistant.yml`) is **only loaded if no agents are defined** in any of the other layers. This ensures that:

- Users who define their own agents don't get polluted with default agents
- Users without any config still get a working default assistant

### Example: Layered Configuration

=== "Global config"

    ```yaml title="~/.config/agentpool/agentpool.yml"
    # User preferences applied everywhere
    model_variants:
      fast:
        type: string
        identifier: openai:gpt-4o-mini
      smart:
        type: anthropic
        identifier: claude-sonnet-4-5
    
    storage:
      providers:
        - type: sql
          url: sqlite:///~/.local/share/agentpool/history.db
    ```

=== "Project config"

    ```yaml title="./agentpool.yml"
    # Project-specific agents (inherits global model_variants and storage)
    agents:
      coder:
        model: smart  # References global variant
        system_prompt: "You are an expert Python developer."
        tools:
          - type: file_access
          - type: bash
    ```

=== "Result"

    The merged config contains both the global `model_variants`/`storage` and the project's `agents`.

### Environment Variables

| Variable | Description |
|----------|-------------|
| `AGENTPOOL_CONFIG` | Path to custom config file |
| `AGENTPOOL_CONFIG_CONTENT` | Inline YAML/JSON config content |
| `AGENTPOOL_NO_GLOBAL_CONFIG` | Set to disable global config loading |
| `AGENTPOOL_NO_PROJECT_CONFIG` | Set to disable project config discovery |

### CLI Commands

Use these commands to inspect config resolution:

```bash
# Show which configs are found and loaded
agentpool config show

# Show standard config paths
agentpool config paths

# Create a starter config file
agentpool config init           # In current project
agentpool config init global    # In global config dir
```

## File-Level Inheritance (INHERIT)

In addition to layered resolution, individual config files can use the `INHERIT` key to explicitly inherit from another file:

=== "Base config"

    ```yaml title="base.yml"
    storage:
      log_messages: true
    agents:
      base_agent:
        model: "openai:gpt-5"
        retries: 2
    ```

=== "Specialized config"

    ```yaml title="agents.yml"
    INHERIT: base-config.yml
    agents:
      my_agent:
        model: openai:gpt-5
        description: "Specialized agent"
    ```

AgentPool supports UPaths (universal-pathlib) for `INHERIT`, allowing pointing to remote configurations (`http://path/to/config.yml`).

## Schema Validation

You can get IDE linter support by adding this line at the top of your YAML:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/phil65/agentpool/refs/heads/main/schema/config-schema.json
```

!!! note
    Versioned config files will arrive soon for better stability!

## Usage

Load a manifest in your code:

```python
from agentpool import AgentPool

async with AgentPool("agents.yml") as pool:
    agent = pool.get_agent("analyzer")
    result = await agent.run("Analyze this code...")
```
