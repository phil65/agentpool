---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool_config/knowledge.py
title: Knowledge Sources
description: Knowledge source configuration and management
icon: material/database
---

Knowledge defines context sources that are loaded when the agent starts: files and URLs
(`paths`) and prompts (`prompts`) whose content is added to the agent's context. Resources that
tools read on demand are configured separately (see [Resources](resources.md)).

```yaml
agents:
  research_assistant:
    model: openai:gpt-5-mini
    knowledge:
      # Files and URLs (any fsspec / UPath location, glob patterns allowed)
      paths:
        - "docs/**/*.md"
        - "https://example.com/api-docs.md"
        - "s3://bucket/context.txt"

      # Prompts whose content is loaded
      prompts:
        - type: file              # From a file (text, markdown or jinja2)
          path: prompts/context.md.j2
          format: jinja2
          description: Project context
        - type: function          # Returned by a Python function
          import_path: myapp.prompts.generate_context
          description: Live context
        - type: text              # Static messages
          description: House rules
          messages:
            - role: system
              content: "Answer in British English."

      convert_to_markdown: true  # Convert documents (PDF, HTML, ...) to markdown
```

## Examples

### Documentation Assistant

```yaml
agents:
  docs_helper:
    model: openai:gpt-5-mini
    knowledge:
      paths: ["docs/**/*.md"]
      prompts:
        - type: file
          path: prompts/docs_context.txt
          description: How the docs are organized
```

### Code Reviewer

```yaml
agents:
  reviewer:
    model: openai:gpt-5-mini
    knowledge:
      paths: ["src/**/*.py", "CONTRIBUTING.md"]
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.knowledge.Knowledge" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="knowledge", header_level=3) }}
///

/// mknodes
{{ "agentpool.prompts.prompts.PromptType" | union_to_markdown(display_mode="yaml", header_style="pymdownx", header_level=3) }}
///
