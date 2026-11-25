---
title: Remote Code Mode Toolset
description: Wrap toolsets for remote code-based interaction
icon: material/cloud-outline
---

# Remote Code Mode Toolset

Similar to [Code Mode](code-mode.md), but the generated code runs in an
[execution environment](../execution-environments.md) (Docker, E2B, Modal, ...).

## Basic Usage

```yaml
agents:
  remote_coder:
    model: openai:gpt-5-mini
    tools:
      - type: remote_code_mode
        environment:
          type: docker
        toolsets:
          - type: file_access
            fs: "file:///workspace"
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.RemoteCodeModeToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///
