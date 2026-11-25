---
title: Multi-Agent Patterns
description: Orchestration patterns for multiple agents
icon: material/sitemap
---

# Multi-Agent Orchestration Patterns

AgentPool offers several patterns for coordinating multiple agents. Each pattern has its strengths and is suited for different use cases.

## Quick Reference

| Pattern | Use Case | Complexity | Type Safety | Visualization | Control Flow |
|---------|----------|------------|-------------|---------------|--------------|
| Direct Connections | Simple A → B flows | Low | ✅ | ❌ | Automatic |
| Controlled Interactions | Interactive/supervised flows | Low | ✅ | ❌ | Manual/Interactive |
| Decision Making (pick) | Agent selection/routing | Low | ✅ | ❌ | Agent-driven |
| Agent as Tool | Hierarchical/expert | Low | ✅ | ❌ | Parent-driven |
| Teams | Parallel/group ops | Medium | ✅ | ❌ | Coordinated |
| Decorator Pattern | Testing/scripted | Medium | ✅ | ❌ | Programmatic |
| Pydantic Graph | Complex workflows | High | ✅ | ✅ | Graph-based |

## 1. Direct Connections (Simple Forwarding)

Best for: Simple linear flows between agents

```python
analyzer = Agent("analyzer")
planner = Agent("planner")
executor = Agent("executor")

# Chain connections
analyzer >> planner >> executor

# Or with configuration
analyzer.connect_to(planner, connection_type="run")
```

## 2. Agent as Tool (Hierarchical)

Best for: Expert consultation patterns where one agent calls others as needed

```python
main_agent = Agent("coordinator")
expert = Agent("expert")

# Register expert as tool
main_agent.register_worker(
    expert,
    name="consult_expert",
    reset_history_on_run=True
)
```

## 3. Teams (Group Operations)

Best for: Parallel execution or group coordination

```python
# Parallel execution
team = Team([agent1, agent2, agent3])
result = await team.execute("Analyze this data")

# Sequential execution (use TeamRun)
run = agent1 | agent2 | agent3
result = await run.run("Process this sequentially")
```

## 4. Decorator Pattern (Testing/Scripting)

Best for: Scripted interactions and testing flows

```python
@with_nodes("analyzer", "planner")
async def analysis_flow(analyzer: Agent, planner: Agent):
    result = await analyzer.run("Analyze this")
    return await planner.run(result.content)
```
