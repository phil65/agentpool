---
title: Tools and Tool Management
description: Tool management and registration
icon: material/tools
---

## Overview

A native agent's tools are managed by its `ToolManager` (`agent.tools`). The manager combines
the tools of several **resource providers**: the agent's own tools, its toolsets, its MCP
servers and the skills it loaded. It handles:

- enabling / disabling tools
- filtering tools by state or name
- temporary tool contexts
- adding and removing providers at runtime

## Passing Tools

The simplest way is to pass callables (or import paths) when creating the agent:

```python
from agentpool import Agent


def my_tool(text: str) -> str:
    """Process some text.

    Args:
        text: Text to process
    """
    return text.upper()


async def main():
    async with Agent(model="openai:gpt-5-mini", tools=[my_tool, "webbrowser.open"]) as agent:
        tools = await agent.tools.get_tools(state="enabled")
        print([tool.name for tool in tools])
```

## Tool Objects

`FunctionTool.from_callable` creates a tool with options:

```python
from agentpool import FunctionTool

tool = FunctionTool.from_callable(
    my_tool,
    name_override="shout",           # Optional name override
    description_override="Does X",  # Optional description
    requires_confirmation=True,     # Ask before running
    meta={"custom": "data"},        # Additional metadata
)
print(tool.name, tool.description, tool.enabled, tool.parameters)
```

## Enabling and Disabling

```python
agent.tools.disable_tool("shout")
agent.tools.enable_tool("shout")
tool = agent.tools.get_tool("shout")
enabled = await agent.tools.get_tools(state="enabled")
agent.tools.reset_states()  # back to the configured states
```

## Temporary Tool Contexts

Tools can be added (or made the only ones) for a block of code:

```python
# Temporarily add tools
async with agent.tools.temporary_tools([tool1, tool2]):
    # tool1 and tool2 are available in addition to the agent's tools
    await agent.run("...")

# Temporarily add tools and disable all others
async with agent.tools.temporary_tools([special_tool], exclusive=True):
    # Only special_tool is available here
    await agent.run("...")

# Original tool states are restored after context exit
```

## Resource Providers

Toolsets are resource providers and can be added at runtime. A `StaticResourceProvider`
groups your own tools:

```python
from agentpool.resource_providers import StaticResourceProvider

provider = StaticResourceProvider(name="my_tools")
provider.register_tool(my_tool)
provider.register_worker(other_agent)  # another agent as a tool
agent.tools.add_provider(provider)
...
agent.tools.remove_provider("my_tools")
```

Toolset configs create their provider with `get_provider()`:

```python
from agentpool_config.toolsets import FSSpecToolsetConfig

agent.tools.add_provider(FSSpecToolsetConfig().get_provider())
```

MCP servers configured on the agent (`mcp_servers`) are connected when the agent enters its
async context; their tools appear in `agent.tools` like any other.
