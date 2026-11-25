---
title: Injection
description: Advanced dependency injection
icon: material/source-merge
---

# Agent Injection and Execution Patterns

AgentPool provides a clean, pytest-inspired way to work with agents. Instead of complex decorators
or string-based configurations, agents are automatically injected as function parameters.

## Basic Agent Injection

Agents defined in your YAML configuration are automatically available as function parameters:

```python
async def analyze_code(
    analyzer: Agent[None],
    reviewer: Agent[None],
    code: str,
) -> CodeAnalysis:
    """Analyze and review code using two agents."""
    analysis = await analyzer.run(f"Analyze this code:\n{code}")
    review = await reviewer.run(f"Review this analysis:\n{analysis.content}")
    return CodeAnalysis(analysis=analysis.content, review=review.content)
```

The function receives fully configured agents matching the parameter names from your YAML:

```yaml
agents:
  analyzer:
    model: gpt-5
    system_prompt: "You are an expert code analyzer..."
  reviewer:
    model: gpt-5
    system_prompt: "You are an expert code reviewer..."
```

## Function Execution Patterns

### Basic Function with Type Safety

The `node_function` decorator marks functions for automatic execution and provides type checking between functions:

```python
@node_function
async def gather_data(
    researcher: Agent[None],
    topic: str,
) -> list[str]:  # Return type is enforced
    """Gather research data."""
    result = await researcher.run(f"Research: {topic}")
    return result.content.split("\n")

@node_function(depends_on="gather_data")
async def analyze_data(
    analyst: Agent[None],
    gather_data: list[str],  # Type must match return type of gather_data
) -> str:
    """Analyze the gathered data."""
    return await analyst.run(f"Analyze these points:\n{'\n'.join(gather_data)}")
```

The system ensures type safety between functions:

- Return types are checked against dependency parameter types
- Runtime type checking of actual values
- Clear error messages for type mismatches

### Sequential Dependencies

Functions can depend on the results of other functions:

```python
@node_function
async def research_topic(
    researcher: Agent[None],
    topic: str,
) -> str:
    return await researcher.run(f"Research: {topic}")

@node_function(depends_on="research_topic")
async def write_article(
    writer: Agent[None],
    research_topic: str,  # Gets typed result from research_topic
) -> str:
    return await writer.run(f"Write article based on:\n{research_topic}")
```

### Parallel Execution

Functions without dependencies can run in parallel:

```python
@node_function
async def expert1_review(
    expert1: Agent[None],
    document: str,
) -> str:
    return await expert1.run(f"Review: {document}")

@node_function
async def expert2_review(
    expert2: Agent[None],
    document: str,
) -> str:
    return await expert2.run(f"Review: {document}")

# Execute both reviews in parallel:
results = await execute_functions(
    [expert1_review, expert2_review],
    pool=pool,
    inputs={"document": "..."},
    parallel=True,
)
```

### Worker Pattern

Register agents as tools for other agents:

```python
@node_function
async def improve_code(
    manager: Agent[None],
    formatter: Agent[None],
    type_checker: Agent[None],
    code: str,
) -> str:
    # Register specialists as tools for manager
    manager.register_worker(formatter)
    manager.register_worker(type_checker)
    return await manager.run(f"Improve this code:\n{code}")
```

## Type Safety

The system provides comprehensive type checking:

```python
# Type mismatch between functions
@node_function
async def get_numbers(
    agent: Agent[None],
) -> list[int]:
    return [1, 2, 3]

@node_function(depends_on="get_numbers")
async def process_data(
    agent: Agent[None],
    get_numbers: str,  # Wrong type! Expected list[int]
) -> str:
    ...  # Raises: TypeError: Type mismatch in process_data: dependency 'get_numbers' is typed as str, but get_numbers returns list[int]

# Runtime type checking
@node_function
async def validate_data(
    agent: Agent[None],
) -> list[str]:
    return 42  # Wrong return type!
    # Raises: TypeError: Type error in validate_data: return value expected list[str], got int
```

Type checking is:

- Optional (untyped functions work normally)
- Enforced between dependencies
- Validated at runtime
- Clear about errors

### Continuous Monitoring

Set up agents for continuous operation:

```python
@node_function
async def monitor_system(
    watcher: Agent[None],
    alerter: Agent[None],
):
    await watcher.run_in_background(
        "Check system status",
        interval=300,  # every 5 minutes
        max_count=None,  # run indefinitely
    )
    watcher.connect_to(alerter)
```

## Tips and Best Practices

1. **Type Hints**: Always use `Agent[None]` or appropriate generic type for proper typing.

2. **Default Values**: Use `Agent[None] | None = None` when agent is optional:

```python
async def optional_review(
    reviewer: Agent[None] | None = None,
    text: str,
) -> str:
    if reviewer:
        return await reviewer.run(f"Review: {text}")
    return text
```

3. **Pool Access**: You can also inject the pool directly:

```python
async def dynamic_team(
    pool: AgentPool,
    task: str,
):
    team = [pool.get_agent(name) for name in ["agent1", "agent2"]]
    group = Team(team)
    return await group.execute(task)
```

4. **Context Sharing**: Use shared dependencies for coordinated agents:

```python
async def shared_analysis(
    analyzer1: Agent[Context],
    analyzer2: Agent[Context],
    context: Context,
):
    group = Team[Context]([analyzer1, analyzer2])
    return await group.execute("Analyze using shared context")
```

The connection between your YAML manifest and the injection system is made through the AgentPool:

```python
from agentpool import AgentPool, AgentsManifest

# 1. Define your agents in YAML
manifest = """
agents:
  researcher:
    model: gpt-5
    system_prompt: "You are an expert researcher..."
  writer:
    model: gpt-5
    system_prompt: "You are an expert writer..."
"""

# 2. Create pool from manifest
async def main():
    async with AgentPool(manifest) as pool:
        # 3. Connect injection system via decorator
        @with_nodes(pool)
        async def research_and_write(
            researcher: Agent[None],  # Will get "researcher" from pool
            writer: Agent[None],      # Will get "writer" from pool
            topic: str,
        ) -> str:
            research = await researcher.run(f"Research: {topic}")
            return await writer.run(f"Write about:\n{research.content}")

        # 4. Use the function - agents are automatically injected
        result = await research_and_write(topic="quantum computing")
```

The key is that the `with_nodes` decorator needs a pool, which is your connection to the manifest. This design:

1. Keeps configuration in YAML (easy to edit/version)
2. Provides clean dependency injection in code
3. Allows flexible pool management strategies
4. Maintains type safety throughout

For more examples and detailed API documentation, refer to the inline docstrings and type hints in the source code.
