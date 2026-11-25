---
title: OpenAPI Toolset
description: Generate tools from OpenAPI specifications
icon: material/api
---

# OpenAPI Toolset

The OpenAPI toolset automatically generates tools from OpenAPI/Swagger specifications, allowing agents to interact with any API that provides an OpenAPI spec.

## Basic Usage

```yaml
agents:
  api_agent:
    model: openai:gpt-5-mini
    tools:
      - type: openapi
        spec: https://api.example.com/openapi.json
```

## Configuration

### From URL

```yaml
tools:
  - type: openapi
    spec: https://petstore.swagger.io/v2/swagger.json
```

### From Local File

```yaml
tools:
  - type: openapi
    spec: ./specs/my-api.yaml
```

### With Authentication

```yaml
tools:
  - type: openapi
    spec: https://api.example.com/openapi.json
    base_url: https://api.example.com/v1
    headers:
      Authorization: "Bearer ${API_TOKEN}"
```

## Generated Tools

Each OpenAPI operation becomes a tool with:

- **Name**: Derived from `operationId` or path
- **Description**: From operation summary/description
- **Parameters**: Mapped from path, query, and body parameters
- **Return type**: Based on response schema

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.OpenAPIToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools") }}
///

## Tips

- Use `namespace` to prefix tool names and avoid collisions
- Provide `base_url` if different from spec's server URL
- Headers support environment variable substitution
