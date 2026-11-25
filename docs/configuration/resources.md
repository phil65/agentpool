---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool/models/manifest.py
title: Resources
description: Filesystems the pool's agents can access (VFS)
icon: material/file-document
---

The manifest's `resources` section mounts filesystems under a name. They are combined into
the pool's virtual filesystem (VFS), which agents access with the [VFS toolset](toolsets/vfs.md).
Every [fsspec](https://filesystem-spec.readthedocs.io/) / UPath filesystem works: local
folders, S3, GCS, GitHub repositories, HTTP, zip files, in-memory filesystems and many more.

```yaml
resources:
  docs: "file://./docs"                 # URI shorthand
  data: "s3://my-bucket/data"
  code: "github://myorg:myrepo@main"
  api:                                  # URI with options
    type: uri
    uri: "https://api.example.com"
    cached: true
  repo:                                 # a typed filesystem config
    type: git
    path: /path/to/repo

agents:
  assistant:
    model: openai:gpt-5-mini
    tools:
      - type: vfs                       # vfs_list, vfs_read, vfs_info over all resources
```

A string is a URI; a config with `type: uri` adds options (`root_path`, `cached`,
`storage_options`). The other types configure a filesystem with typed fields.

## Available Filesystems

/// mknodes
{{ "upathtools_config.FilesystemConfigType" | config_union_table }}
///

## Configuration Reference

/// mknodes
{{ "upathtools_config.base.URIFileSystemConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="name", header_level=3) }}
///

/// mknodes
{{ "upathtools_config.FilesystemConfigType" | union_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="name", header_level=3) }}
///
