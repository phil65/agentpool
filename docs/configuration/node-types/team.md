---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool_config/teams.py
title: Teams
description: Team configuration and setup
icon: lucide/users
---

# Team Configuration

Teams and sequential chains in AgentPool allow you to create sophisticated message flows by composing message nodes. Any node (Agent, Team, or TeamRun) can be part of a team or chain, allowing for arbitrarily complex nested structures.

## Example Configuration

```yaml
agents:
  analyzer:
    model: openai:gpt-5
    system_prompt: "You analyze code for issues."

  planner:
    model: openai:gpt-5
    system_prompt: "You create fix plans."

  executor:
    model: openai:gpt-5
    system_prompt: "You implement fixes."

teams:
  analysis_team:
    mode: parallel  # Run members in parallel
    members:
      - analyzer
      - planner
    shared_prompt: "Focus on performance issues."  # Added to all members' prompts
    connections:
      - type: node
        name: executor
        wait_for_completion: true

  review_chain:
    mode: sequential  # Run members in sequence
    members:
      - analysis_team  # Teams can be members
      - executor
    shared_prompt: "This is a critical production system."

  monitoring:
    mode: parallel
    members:
      - review_chain  # Chains can be members
      - performance_monitor
    connections:
      - type: file
        path: "logs/team_output.txt"
```

## Components

### Mode

- `parallel`: Members execute simultaneously (a `Team`)
- `sequential`: Members execute in sequence (a `TeamRun`)

### Members

- References to agents or other teams
- Can include any message node type:

  ```yaml
  members:
    - single_agent  # Individual agent
    - parallel_team # Team
    - processing_chain  # TeamRun
  ```

### Shared Prompt

Additional context provided to all team members:

```yaml
teams:
  security_team:
    mode: parallel
    members: [analyzer, validator]
    shared_prompt: "Focus on security implications."  # Added to each member's input
```

### Connections

Message forwarding configuration:

```yaml
connections:
  - type: node
    name: final_reviewer
    queued: true
    queue_strategy: latest
    wait_for_completion: true
    filter_condition:
      type: word_match
      words: ["critical", "urgent"]
```

## Nesting Capabilities

Teams can be arbitrarily nested to create complex workflows:

```yaml
teams:
  analysis:
    mode: parallel
    members: [analyzer, planner]

  execution:
    mode: sequential
    members: [validator, executor]

  workflow:
    mode: sequential
    members:
      - analysis  # Parallel team
      - execution  # Sequential chain
    connections:
      - type: node
        name: final_review
```

## Connection Control

### Message Flow

- `wait_for_completion`: Whether to wait for target to complete
- `queued`: Queue messages for manual processing
- `queue_strategy`: How to handle queued messages (latest/concat/buffer)
- `parallel`: Process a message together with the source's neighbouring parallel
  connections (their runs overlap) instead of one connection after another
- `stats_history`: Messages the connection's stats keep (default 100; `null`: all)

### Filtering

```yaml
filter_condition:
  type: word_match
  words: ["urgent", "critical"]
  case_sensitive: false
```

### Transformation

```yaml
transform: myapp.transforms.process_message  # Python callable
```

### Monitoring

All teams provide statistics:

- Message count
- Token usage
- Execution timing
- Cost tracking

## Example Complex Workflow

```yaml
teams:
  # Initial analysis (parallel)
  analysis:
    mode: parallel
    members: [code_analyzer, security_checker]
    shared_prompt: "Focus on production impact."

  # Planning chain (sequential)
  planning:
    mode: sequential
    members: [issue_classifier, fix_planner]
    shared_prompt: "Consider dependencies."

  # Execution group (parallel)
  execution:
    mode: parallel
    members: [code_fixer, test_runner]

  # Complete workflow
  full_pipeline:
    mode: sequential
    members:
      - analysis  # Parallel analysis
      - planning  # Sequential planning
      - execution  # Parallel execution
    connections:
      - type: node
        name: final_reviewer
        wait_for_completion: true
      - type: file
        path: "reports/{date}_workflow.txt"
```

This configuration creates a sophisticated workflow where:

1. Analysis runs in parallel
2. Planning happens sequentially
3. Execution runs in parallel again
4. Results are reviewed and logged

All while maintaining type safety and providing comprehensive monitoring.
