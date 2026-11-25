---
title: Response Types
description: Structured response type configuration
icon: material/reply
---

Response types define structured output formats for agents. They can be defined directly in YAML or imported from Python code.

!!! tip "Type Safety"
    While YAML configuration is convenient, defining response types as Pydantic models in Python code provides better type safety, IDE support, and reusability:

    ```python
    from pydantic import BaseModel

    class AnalysisResult(BaseModel):
        success: bool
        issues: list[str]
        severity: str
    ```

## Response Configuration

Each response definition includes:

```yaml
responses:
  MyResponse:
    response_schema:  # Schema definition (required)
      type: "inline"  # or "import"
      fields:
        answer:
          type: str
    description: "Optional description of the response"
    result_tool_name: "final_result"  # Tool name for result creation
    result_tool_description: "Create the final result"  # Tool description
    output_retries: 3  # Number of validation retries
```

## Inline Responses

Define response structure directly in YAML:

```yaml
--8<-- "docs/configuration/responses.yml"
```

## Imported Responses

Import response types from Python code:

### Python Type Import

```yaml
responses:
  AdvancedAnalysis:
    response_schema:
      type: "import"
      import_path: "myapp.types:AnalysisResult"
  MetricsResult:
    response_schema:
      type: "import"
      import_path: "myapp.analysis:MetricsResponse"
```

### Using Response Types

### Assign to Agent

```yaml
agents:
  analyzer:
    model: "openai:gpt-5"
    output_type: "CodeAnalysis"  # Reference response by name
```

## Available Field Types

- `str`: Text strings
- `int`: Integer numbers
- `float`: Floating point numbers
- `bool`: Boolean values
- `list[type]`: Lists of values (e.g., `list[str]`, `list[int]`)
- `dict[key_type, value_type]`: Dictionaries
- `datetime`: Date and time values
- Custom types through imports

## Configuration Reference

/// mknodes
{{ "agentpool_config.output_types.StructuredResponseConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="MyResponse", header_level=3) }}
///

/// mknodes
{{ "agentpool_config.output_types.SchemaDef" | union_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="response_schema", header_level=3) }}
///
