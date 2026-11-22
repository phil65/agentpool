---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool_config/nodes.py
title: Agent
description: Agent configuration options
icon: material/robot
---

Individual agent configurations define the behavior, capabilities, and settings for each agent in your manifest. Each agent entry in the `agents` dictionary represents a complete agent setup.

## Overview

Agent configuration includes:

- **Model settings**: LLM provider and model selection
- **System prompts**: Define agent behavior and personality
- **Tools and toolsets**: Capabilities available to the agent
- **Knowledge sources**: Context and information access
- **Output types**: Structured response definitions
- **Workers**: Sub-agents for delegation
- **Connections**: Message routing to other nodes
- **MCP servers**: Model Context Protocol integrations
- **Triggers**: Event-based activation

## Configuration Reference

/// mknodes
{{ "agentpool.models.agents.NativeAgentConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False) }}
///

## Configuration Inheritance

Agents can inherit configuration from other agents or base configurations:

```yaml
agents:
  base_agent:
    model: "openai:gpt-4o"
    retries: 2
    tools:
      - type: "resource_access"
  
  specialized_agent:
    model: "openai:gpt-4o"
    description: "Specialized version"
    system_prompt: "You are a specialized agent..."
```


## Agents Section

Complete example of an agent configuration:

```yaml
agents:
  web_assistant:                   # Name of the agent
    description: "Helps with web tasks"  # Optional description
    model: openai:gpt-5           # Model to use
    tools:
      open_browser:
        import_path: webbrowser.open
        description: "Opens URLs in browser"
    system_prompt:
      - "You are a web assistant."
      - "Use open_browser to open URLs."
    retries: 2                   # Number of retries for failed
```

## Field Reference

| Field Name | Description |
|------------|-------------|
| [`name`](agent.md#name) | Identifier for the agent (set from dict key, not from YAML) |
| [`config_file_path`](agent.md#config-file-path) | Config file path for resolving relative paths |
| [`display_name`](agent.md#display-name) | Human-readable display name for the agent |
| [`description`](agent.md#description) | Optional description of the agent |
| [`triggers`](event-sources.md) | Event sources that activate this agent |
| [`connections`](../connections.md) | Targets to forward results to |
| [`mcp_servers`](../mcp.md) | List of MCP server configurations |
| [`input_provider`](agent.md#input-provider) | Provider for human-input-handling |
| [`event_handlers`](observability.md#event-handlers) | Event handlers for processing agent stream events |
| [`model`](model.md) | The model to use for this agent |
| [`toolsets`](../toolsets/index.md) | Toolset configurations for extensible tool collections |
| [`session`](session.md) | Session configuration for conversation recovery |
| [`output_type`](responses.md) | Name of the response definition to use |
| [`retries`](agent.md#retries) | Number of retries for failed operations |
| [`output_retries`](agent.md#output-retries) | Max retries for result validation |
| [`end_strategy`](agent.md#end-strategy) | The strategy for handling multiple tool calls when a final result is found |
| [`avatar`](agent.md#avatar) | URL or path to agent's avatar image |
| [`system_prompts`](../prompts.md) | System prompts for the agent |
| [`knowledge`](../knowledge.md) | Knowledge sources for this agent |
| [`workers`](../toolsets/workers.md) | Worker agents which will be available as tools |
| [`requires_tool_confirmation`](agent.md#tool-confirmation) | How to handle tool confirmation (always/never/per_tool) |
| [`debug`](agent.md#debug) | Enable debug output for this agent |
| [`environment`](execution-environments.md) | Execution environment configuration for this agent |
| [`usage_limits`](agent.md#usage-limits) | Usage limits for this agent |
| [`tool_mode`](agent.md#tool-mode) | Tool execution mode (None/codemode) |


## Related Configuration

- [Model Configuration](../model.md) - Configure LLM providers
- [System Prompts](../prompts.md) - Define agent behavior
- [Toolsets](../toolsets/index.md) - Tool collections
- [Workers](../toolsets/workers.md) - Sub-agent delegation
- [Connections](../connections.md) - Message routing
- [MCP Servers](../mcp.md) - MCP integration
- [ACP Agents](acp-agents.md) - External ACP agent integration
- [AG-UI Agents](agui-agents.md) - AG-UI protocol agents
- [T3 Code Agents](t3-agents.md) - Threads in a T3 Code environment
