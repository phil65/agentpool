---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool/models/file_agents.py
title: File Agents
description: Agents defined in markdown files (Claude Code, OpenCode or AgentPool format)
icon: material/file-account
---

`file_agents` loads agents from markdown files with YAML frontmatter: the frontmatter
configures the agent, the markdown body becomes its system prompt. Files in the format of
Claude Code subagents (`.claude/agents/*.md`), OpenCode agents and AgentPool's own format
(any native agent field in the frontmatter) are supported.

```yaml
file_agents:
  reviewer: .claude/agents/reviewer.md   # format detected from the frontmatter
  debugger:
    type: opencode                       # explicit format
    path: ./agents/debugger.md
  remote: https://example.com/agents/helper.md  # any UPath location
```

```markdown title="agents/worker.md"
---
model: openai:gpt-5-nano
tools:
  - type: file_access
---
You are a file processing worker.
```

The same file can be used for several agents (`worker_1: agents/worker.md`,
`worker_2: agents/worker.md`).

## Configuration Reference

/// mknodes
{{ "agentpool.models.file_agents.FileAgentConfigTypes" | union_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="name", header_level=3) }}
///
