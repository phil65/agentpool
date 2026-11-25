---
title: Tasks
description: Task definition and configuration
icon: material/format-list-checks
---

# Task Configuration

Tasks (called "jobs" in YAML) define reusable operations that agents can execute. They can be defined in YAML and include:

- Prompt templates
- Required tools
- Knowledge sources
- Result type validation

Jobs are run with `agentpool task <agent> <job>` or `pool.get_job(name)` in code.

## Basic Task

Simple task with prompt and result type:

```yaml
jobs:
  analyze_code:
    prompt: "Analyze the code in src directory for potential improvements"
    required_output_type: "myapp.types:AnalysisResult"
    description: "Analyze code quality and suggest improvements"
```

## Knowledge Sources

Tasks can load context from various sources:

```yaml
jobs:
  summarize_docs:
    prompt: "Summarize the documentation changes"
    required_output_type: "myapp.types:DocSummary"
    knowledge:
      # Files and URLs
      paths:
        - "docs/**/*.md"
        - "README.md"

      # Context prompts
      prompts:
        - type: text
          description: Documentation standards
          messages:
            - role: system
              content: "Consider the project's documentation standards..."
```

## Required Tools

Specify tools needed for the task:

```yaml
jobs:
  security_audit:
    prompt: "Perform security audit on the codebase"
    required_output_type: "myapp.types:AuditReport"
    tools:
      - "myapp.tools.security.analyze_dependencies"  # import path
      - name: "custom_scanner"
        import_path: "myapp.tools.security.scan_code"
        description: "Custom security scanner"
```

## Dependencies and Context

Tasks can require specific data:

```yaml
jobs:
  review_pr:
    prompt: "Review the pull request changes"
    required_output_type: "myapp.types:ReviewResult"
    required_dependency: "myapp.types:PRContext"  # Type hint for required context
    min_context_tokens: 1000       # Minimum context window size
    requires_vision: false         # Whether vision capability is needed
```


## Complete Example

Complex task with all features:

```yaml
jobs:
  deep_code_review:
    description: "Perform comprehensive code review"
    prompt: "Review the code changes focusing on:"
    required_output_type: "myapp.types:ReviewResult"
    required_dependency: "myapp.types:CodeContext"
    requires_vision: false

    # Required knowledge
    knowledge:
      paths: ["src/**/*.py", "CONTRIBUTING.md"]

    # Required tools
    tools:
      - "myapp.tools.analyze_complexity"
      - import_path: "myapp.tools.metrics"

    # Context requirements
    min_context_tokens: 2000
```

## Task Registry

All tasks are available through the agent pool:

```python
# Get job definition
job = pool.get_job("analyze_code")

# Register new task
from agentpool_config.task import Job
job = Job(
    name="new_task",
    description="A new task",
    prompt="Do something interesting",
)
pool.register_task("new_task", job)
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.task.Job" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="job_name", header_level=3) }}
///
