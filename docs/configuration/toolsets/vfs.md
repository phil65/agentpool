---
title: VFS Toolset
description: Access manifest-defined resources
icon: material/folder-network
---

# VFS Toolset

The VFS (Virtual File System) toolset provides access to resources defined in your manifest's `resources` section. It offers a unified interface to query and read from multiple configured filesystems.

## Basic Usage

First, define resources in your manifest:

```yaml
resources:
  docs:
    type: uri
    uri: "memory://"
  data: "s3://my-bucket/data"
  code: "github://myorg:myrepo@main"
```

Then enable the VFS toolset:

```yaml
agents:
  my_agent:
    model: openai:gpt-5-mini
    tools:
      - type: vfs
```

## Available Tools

```python exec="true"
from agentpool_toolsets.vfs_toolset import VFSTools
from agentpool.docs.utils import generate_tool_docs

toolset = VFSTools()
print(generate_tool_docs(toolset))
```

## Usage Examples

Once configured, agents can access resources like:

```
# List all available resources
vfs_list()

# List files in a specific resource
vfs_list("docs")

# Read a file
vfs_read("docs/guide.md")

# Read a directory recursively
vfs_read("code/src", recursive=True)
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.VFSToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///

## When to Use VFS vs File Access

| Use Case | Recommended |
|----------|-------------|
| Shared resources across agents | VFS |
| Agent-specific filesystem | File Access |
| Simple resource access | VFS |
| Full file editing capabilities | File Access |
| Composed/mounted filesystems | File Access with `mounts` |
