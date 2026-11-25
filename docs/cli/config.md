---
title: config
description: Configuration management and diagnostics
icon: lucide/settings
---

The `config` command group helps you understand and manage AgentPool's layered configuration system.

## Overview

AgentPool automatically discovers and merges configuration from multiple sources:

1. **Global config** (`~/.config/agentpool/agentpool.yml`)
2. **Custom config** (`AGENTPOOL_CONFIG` environment variable)
3. **Project config** (`agentpool.yml` in project/git root)
4. **Explicit config** (CLI argument)

These commands help you inspect which configs are being loaded and create new ones.

## Commands

/// mknodes
{{ "agentpool.__main__:cli"| MkCliDoc(prog_name="config", show_subcommands=True) }}
///

## Examples

### Inspect Configuration

```bash
# Show which config files are found and what they contain
agentpool config show

# Show with a specific explicit config included
agentpool config show my-agents.yml

# Output as YAML for scripting
agentpool config show --format yaml
```

### View Config Paths

```bash
# Show where AgentPool looks for config files
agentpool config paths
```

### Create New Config

```bash
# Create a starter config in current project
agentpool config init

# Create a global config for user-wide preferences
agentpool config init global

# Create at a specific path
agentpool config init ./configs/my-agents.yml

# Overwrite existing config
agentpool config init --force
```

## Use Cases

### Setting Global Preferences

Create a global config to set preferences that apply to all projects:

```bash
agentpool config init global
```

Then edit `~/.config/agentpool/agentpool.yml`:

```yaml
# Global preferences
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

### Project-Specific Agents

Create a project config that inherits global settings:

```bash
agentpool config init
```

Then edit `./agentpool.yml`:

```yaml
agents:
  coder:
    model: smart  # Uses global model_variant
    system_prompt: "You are an expert in this codebase."
    tools:
      - type: file_access
      - type: bash
```

### Debugging Config Issues

If your agent isn't behaving as expected, check which configs are being loaded:

```bash
agentpool config show
```

This shows:
- Which config files were found
- What keys each layer contributes
- The final merged result
