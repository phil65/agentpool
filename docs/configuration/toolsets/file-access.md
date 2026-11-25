---
title: File Access Toolset
description: Read, write, and edit files on any filesystem
icon: material/file-document
---

# File Access Toolset

The File Access toolset provides tools for reading, writing, and editing files on any fsspec-compatible filesystem. This includes local files, S3, GitHub repositories, and more.

## Basic Usage

```yaml
agents:
  my_agent:
    model: openai:gpt-5-mini
    tools:
      - type: file_access
        fs: "file:///workspace"
```

## Filesystem Options

The `fs` field accepts either a URI string or a full filesystem configuration:

### URI String

```yaml
tools:
  - type: file_access
    fs: "file:///home/user/project"
```

### Filesystem Config

```yaml
tools:
  - type: file_access
    fs:
      type: github
      org: sveltejs
      repo: svelte
      sha: main
```

### Composed Filesystems

Mount multiple filesystems together using the `mounts` type:

```yaml
tools:
  - type: file_access
    fs:
      type: mounts
      mounts:
        docs: "github://sveltejs:svelte@main"
        src: "file:///workspace/src"
        data:
          type: s3
          bucket: my-bucket
```

## Available Tools

```python exec="true"
from agentpool_toolsets.fsspec_toolset import FSSpecTools
from agentpool.docs.utils import generate_tool_docs

toolset = FSSpecTools()
print(generate_tool_docs(toolset))
```

## Custom filesystems and security

With an `fs` (any filesystem that is not an execution environment's), every tool of the
toolset (`read`, `list_directory`, `grep`, `write`, the edit tools, `delete_path`,
`download_file`) works on that filesystem only, never on the agent's environment:

- relative paths resolve against the toolset's `cwd` only (not the agent env's working directory),
- `grep` searches in-process (no `rg`/`grep` subprocess, which would search the host),
- post-write diagnostics and the document converter are skipped (they read host paths).

`download_file` verifies TLS certificates (there is no opt-out). URLs without a usable
file name (`/`, `/.`, `/..`) are saved as `downloaded_file`.

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.FSSpecToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///

## Examples

### Local Development

```yaml
tools:
  - type: file_access
    fs: "file:///home/user/project"
    max_file_size_kb: 128
```

### GitHub Repository Access

```yaml
tools:
  - type: file_access
    fs:
      type: github
      org: fastapi
      repo: fastapi
      cached: true
```

### Multi-Source Documentation

```yaml
tools:
  - type: file_access
    fs:
      type: mounts
      mounts:
        svelte: "github://sveltejs:svelte@main"
        react: "github://facebook:react@main"
        local: "file:///docs"
```
