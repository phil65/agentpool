---
title: Document Conversion
description: Document conversion features
icon: material/file-document
---

# Document Conversion

AgentPool provides document conversion capabilities to transform various document formats (PDFs, Office documents, HTML, etc.)
into markdown text that can be processed by language models.

## Overview

The conversion system:

- Supports multiple document formats through different converters
- Can handle both files and raw content
- Uses async I/O for file operations
- Includes a fallback plain text converter

## Usage

### Through Conversation Manager

Documents can be added to an agent's context:

```python
# Add document from path
await agent.conversation.add_context_from_path(
    "document.pdf",
    convert_to_md=True  # Enable markdown conversion
)

# Add raw text
agent.conversation.add_context_message(text, source="notes.txt")
```

### Automatic Conversion

When passing paths to `Agent.run()`, documents are automatically converted if needed:

```python
# Will convert PDF if model doesn't support it directly
await agent.run(Path("document.pdf"))

# Multiple inputs
await agent.run(
    "Analyze this document:",
    Path("document.pdf"),
    "And compare it with:",
    Path("other.docx")
)
```

## Configuration

Document conversion is configured globally in the manifest's `conversion` section; the
converters come from [docler](https://github.com/phil65/docler):

```yaml
conversion:
  default_provider: markitdown
  max_size: 10485760      # bytes
  providers:
    - type: markitdown
    - type: marker
```

The same converter configs are used by toolsets that convert documents (for example the
`file_access` toolset's `conversion` option).

/// mknodes
{{ "agentpool_config.converters.ConversionConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="conversion", header_level=3) }}
///

## Available Converters

/// mknodes
{{ "docler_config.ConverterConfig" | config_union_table }}
///

/// mknodes
{{ "docler_config.ConverterConfig" | union_to_markdown(display_mode="yaml", header_style="pymdownx", header_level=3, wrapped_in="providers") }}
///
