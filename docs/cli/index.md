---
title: CLI
description: Command-line interface overview
icon: lucide/terminal
order: 4
---

The AgentPool CLI provides a comprehensive set of commands to manage and interact with AI agents.
It's designed around the concept of an "active agent file" - a YAML configuration that defines your agents and their settings.
This avoids the need to pass the config file path each time you want to run a command.

## Active Agent File

The CLI maintains an "active agent file" setting which determines which agents are available for commands like `run`, `task`, or `watch`.
You can:

- Add agent files with `agentpool add <name> <path>`
- Set the active file with `agentpool set <name>`
- List agents from the active config with `agentpool list-agents`

Most commands will use the currently active agent file by default, but can be overridden with the `--config` option.

## Available Commands

/// mknodes
{{ "agentpool.__main__:cli" | cli_command_table(link_prefix="./") }}
///

Global options (before the command): `--log-level` / `-l` (`debug`, `info`, `warning`,
`error`) and `--version` / `-v`.

## Quick Start

### Option 1: Automatic Config Discovery

AgentPool automatically discovers config files. Just create `agentpool.yml` in your project:

```bash
# Create a starter config
agentpool config init

# Edit the config, then run
agentpool run myagent "Hello"
```

### Option 2: Named Configurations

For managing multiple config files:

1. Add and activate an agent configuration:

   ```bash
   agentpool add myconfig agents.yml
   agentpool set myconfig
   ```

2. List available agents:

   ```bash
   agentpool list-agents
   ```

3. Run a prompt with an agent:

   ```bash
   agentpool run analyzer "Analyze this text"
   ```

## Command Examples

### Running Agents

```bash
# Run a single agent with a prompt
agentpool run myagent "Analyze this"

# Run a team
agentpool run myteam "Process this"

# Show detailed output with costs
agentpool run myagent "Hello" --detail detailed --costs
```

### Executing Tasks

```bash
# Execute a defined task
agentpool task docs write_api_docs

# Execute with additional prompt
agentpool task docs write_api_docs --prompt "Include code examples"
```

### Server Commands

```bash
# ACP server for IDE integration (Zed, Toad)
agentpool serve-acp config.yml

# OpenCode server for OpenCode TUI/Desktop
agentpool serve-opencode config.yml --port 4096

# MCP server (stdio transport)
agentpool serve-mcp config.yml

# MCP server over Streamable HTTP
agentpool serve-mcp config.yml --transport streamable-http --port 3001

# AG-UI server
agentpool serve-agui config.yml --port 8002

# OpenAI-compatible API server
agentpool serve-api config.yml --port 8000

# Vercel AI Data Stream Protocol
agentpool serve-vercel config.yml

# OpenCode TUI with an integrated server
agentpool ui opencode config.yml
```

### History Commands

```bash
# Show last 5 conversations
agentpool history show -n 5

# Show conversations from last 24 hours
agentpool history show --period 24h

# Show stats grouped by model
agentpool history stats --period 1w --group-by model

# Clear history for specific agent
agentpool history reset --agent myagent
```

## Configuration Files

Agent configurations are YAML files that define:

- Available agents and their capabilities
- System prompts and knowledge sources
- Tool configurations
- Response types
- And more

Example:

```yaml
agents:
  analyzer:
    display_name: "Text Analyzer"
    model: openai:gpt-5-mini
    description: "Analyzes text and provides structured output"
    tools:
      - type: file_access
```

See the [Configuration Guide](../configuration/index.md) for detailed information about agent configuration.
