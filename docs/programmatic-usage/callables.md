---
title: Callables as Agents
description: Using Python callables as agents
icon: material/code-braces
---

# Callables as First-Class Citizens

Regular Python functions (callables) are treated as first-class citizens and can be used interchangeably with agents in most contexts. This enables seamless integration of custom processing functions into agent workflows.

## Direct Agent Creation

You can create agents directly from callables, preserving their type information:

```python
# Simple untyped function becomes Agent[None, str]
def process(message: str) -> str:
    return message.upper()

agent = Agent.from_callback(process)

# Typed function becomes Agent[None, ResultType]

class AnalysisResult(BaseModel):
    sentiment: float
    topics: list[str]

def analyze(message: str) -> AnalysisResult:
    return AnalysisResult(sentiment=0.8, topics=["AI"])

agent = Agent.from_callback(analyze)
```

## Automatic Conversion in Workflows

Callables are automatically converted to agents when used in:

### Teams (using `&`)

```python
def analyze(text: str) -> AnalysisResult:
    return AnalysisResult(...)

def summarize(text: str) -> str:
    return "Summary: " + text

# Both functions become agents in the team
team = analyzer & analyze & summarize
```

### Pipelines (using `|`)

```python
# Functions become agents in the pipeline
pipeline = agent | analyze | summarize
```

### Connections (using `>>`)

```python
# Function becomes agent when used as target
agent >> analyze
```

## Context injection

Functions can optionally accept agentContext:

```python
def process(ctx: AgentContext, message: str) -> str:
    # Access agent capabilities, configuration, etc.
    return f"Processed by {ctx.node_name}: {message}"

# Context is automatically injected
agent = Agent(provider=process)
```

This seamless integration of callables allows you to:

- Mix and match agents with regular functions
- Create lightweight processing steps without full agent overhead
- Preserve type safety throughout the workflow
- Gradually convert functions to full agents as needed

## Callables for prompts

AgentPool also allows to pass Callables for user prompts:

```python
def my_user_prompt() -> str:
    return f"Current time: {datetime.now()}"

agent.run(my_user_prompt)
```

!!! note
    System prompts are evaluated once when the agent enters its async context and remain fixed for the agent's lifetime. This enables prompt caching.
