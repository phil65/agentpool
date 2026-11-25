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

## OpenCode permissions

The `permission` block of an OpenCode agent file becomes `pre_tool_use` hooks
(`agentpool.hooks.permission.fixed_decision`) for the tools each key covers:

| Key | AgentPool tools |
|---|---|
| `edit` | `write`, `edit`, `agentic_edit`, `edit_batch`, `multi_edit`, `regex_replace_lines`, `apply_patch`, `patch`, `delete_path` |
| `bash` | `bash`, `sandbox_bash`, `execute_command`, `execute_code` |
| `webfetch` | `webfetch`, `web_fetch`, `fetch`, `download_file` |

`deny` blocks the calls (the model is told), `ask` asks the user before every call, `allow`
keeps the agent's confirmation mode. Command patterns (`bash: {"git diff": allow, "*": ask}`)
are not supported: the strictest decision of the map applies to the whole tool.

## Configuration Reference

/// mknodes
{{ "agentpool.models.file_agents.FileAgentConfigTypes" | union_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="name", header_level=3) }}
///
