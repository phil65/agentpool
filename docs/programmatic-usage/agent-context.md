---
title: Agent Context
description: Agent context for tool cools
icon: material/folder-cog
---

## Overview

AgentContext provides an easy way to access the agent as well as the pool in tool calls. It can get requested separately from Pydantic-AIs `RunContext`, which focuses on the Context of the actual agent run.


## Basic Usage

Tools can request context injection by adding an `AgentContext` parameter:

```python
async def my_tool(ctx: AgentContext[TDeps], arg: str) -> str:
    """Tool with access to context and dependencies."""
    # Access pool functionality
    sub_agent = ctx.pool.get_agent("helper")
    result = await sub_agent.run(arg)
    return do_something_with_result(result)
```

It is also possible to request both contexts, in this case the Pydantic-AI `RunContext` needs to appear first in the signature.

```python
async def my_tool(run_ctx: RunContext, agent_ctx: AgentContext, arg: str) -> str:
    """Tool which requires both contexts."""
```
