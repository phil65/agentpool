---
title: Inheritance
description: Configuration inheritance system
icon: material/source-merge
---

AgentPool supports YAML file inheritance using Yamling, making configurations more reusable and maintainable.

## YAML File Inheritance

Using Yamling's inheritance system, entire YAML files can inherit from other files:


=== "Base config"

    ```yaml title="base.yml"
    agents:
      assistant:
        model: openai:gpt-5
        tools:
          - type: vfs
    
    storage:
      providers:
        - type: sql
          url: sqlite:///history.db
    ```

=== "Extended config"

    ```yaml title="agents.yml"
    INHERIT: base.yml  # Inherit entire base configuration
    
    agents:
      # Add new agents
      code_assistant:
        model: openai:gpt-5
        description: "Specializes in code review"
        system_prompt: "Focus on code quality and best practices."
        tools:
          - type: execute_code
    ```


### Remote File Inheritance

Yamling supports UPath, allowing inheritance from remote files:

```yaml
# Inherit from remote sources
INHERIT:
  - base.yml
  - https://example.com/base_config.yml
  - s3://my-bucket/configs/agents.yml
  - git+https://github.com/org/repo/config.yml
```

## Inheritance Resolution

1. Load all inherited files in order
2. Merge configurations:
   - Later files override earlier ones
   - Lists and dictionaries are merged
   - Complex fields use smart merging

## Reusing Agent Definitions

For reusing agent configurations, use **file agents** - define an agent in a markdown file and reference it multiple times:

```yaml title="config.yml"
file_agents:
  worker_1: agents/worker.md
  worker_2: agents/worker.md  # Same definition, different instance
  worker_3: agents/worker.md
```

```markdown title="agents/worker.md"
---
model: openai:gpt-6-luna
tools:
  - type: file_access
---
You are a file processing worker.
```

This creates three separate agent instances with identical configurations, which is cleaner than inheritance for simple duplication.

Inheritance in AgentPool helps maintain DRY (Don't Repeat Yourself) configurations while allowing for flexible customization.
