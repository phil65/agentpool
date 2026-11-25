---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool_config/observability.py
title: Observability
description: Monitoring and tracing configuration
icon: material/monitor-eye
---

Observability providers enable monitoring, tracing, and logging of agent operations. This helps you understand agent behavior, track performance, and debug issues in production.

## Overview

AgentPool integrates with leading observability platforms:

- **Logfire**: Pydantic's observability platform with native support for structured logging
- **Langsmith**: LangChain's tracing and evaluation platform
- **AgentOps**: Specialized platform for agent monitoring and analytics
- **Arize Phoenix**: Open-source observability for LLM applications
- **Custom**: Integrate your own observability solution

These integrations provide automatic instrumentation of agent operations, capturing traces, spans, and metrics without code changes.

```yaml
observability:
  enabled: true
  provider:
    type: logfire
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.observability.ObservabilityConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="observability", header_level=3) }}
///

/// mknodes
{{ "agentpool_config.observability.ObservabilityProviderConfig" | union_to_markdown(display_mode="yaml", header_style="pymdownx") }}
///

## Key Features

- **Automatic tracing**: All agent operations are automatically traced
- **Structured logging**: Rich context and metadata for every operation
- **Performance metrics**: Token usage, latency, and cost tracking
- **Error tracking**: Detailed error capture with stack traces and context
- **Multi-provider support**: Use multiple observability platforms simultaneously

## Use Cases

- **Development**: Debug agent behavior and optimize prompts
- **Production monitoring**: Track performance and reliability metrics
- **Cost optimization**: Analyze token usage and identify expensive operations
- **Quality assurance**: Evaluate agent responses and identify issues
- **Compliance**: Maintain audit logs of agent interactions

## Configuration Notes

- Providers can be configured at the manifest level for global observability
- API keys should be stored in environment variables for security
- Multiple providers can be active simultaneously
- Each provider captures different metrics and views of agent behavior
- Some providers offer additional features like prompt optimization and A/B testing
