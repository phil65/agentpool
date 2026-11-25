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

## Example

```yaml
agents:
  web_assistant:                     # Name of the agent
    description: "Helps with web tasks"
    model: openai:gpt-5-mini         # Model to use (string or model config)
    tools:
      - type: import
        import_path: webbrowser.open
        name: open_browser
        description: "Opens URLs in browser"
    system_prompt:
      - "You are a web assistant."
      - "Use open_browser to open URLs."
    retries: 2                       # Retries for failed tool calls / validations
```

To share settings between agents, use [`INHERIT`](../inheritance.md) or
[model variants](../model.md).

## Field Reference

/// mknodes
{{ "agentpool.models.agents.NativeAgentConfig" | config_fields_table(exclude=["name"]) }}
///

## Related Configuration

- [Model Configuration](../model.md) - Configure LLM providers
- [Tools](../tools.md) and [Toolsets](../toolsets/index.md) - Capabilities of the agent
- [System Prompts](../prompts.md) - Define agent behavior
- [Workers](../toolsets/workers.md) - Sub-agent delegation
- [Connections](../connections.md) - Message routing
- [MCP Servers](../mcp.md) - MCP integration
- [Session](../session.md) - Loading earlier conversations
- [Execution Environments](../execution-environments.md) - Where the agent's tools run
