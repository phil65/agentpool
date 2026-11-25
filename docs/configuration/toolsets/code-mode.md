---
title: Code Mode Toolset
description: Wrap toolsets for code-based interaction
icon: material/code-tags
---

# Code Mode Toolset

Wraps other toolsets to enable code-based tool invocation, allowing agents to call tools by generating code.

## Basic Usage

```yaml
agents:
  coder:
    model: openai:gpt-5-mini
    tools:
      - type: code_mode
        toolsets:
          - type: file_access
            fs: "file:///workspace"
```

## How It Works

Instead of calling tools directly, the agent generates Python code that invokes the wrapped tools. This enables more complex tool compositions and programmatic control flow.

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.CodeModeToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///
