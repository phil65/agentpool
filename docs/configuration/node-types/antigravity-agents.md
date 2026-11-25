---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentpool/models/antigravity_agents.py
title: Antigravity Agents
description: Run Google Antigravity SDK agents with built-in coding tools, policies, and MCP bridging
icon: simple/google
---

Antigravity agents wrap Google's official [`google-antigravity`](https://github.com/google-antigravity/antigravity-sdk-python) Python SDK. They run Antigravity's local harness with built-in file, search, shell, web, and subagent tools, multimodal inputs, native structured output, and policy-driven tool approvals, while exposing AgentPool's toolsets and MCP servers over Streamable HTTP MCP.

## Installation

`google-antigravity` is an optional extra:

```bash
uv pip install "agentpool[antigravity]"
```

## Authentication

Antigravity supports three backend modes:

- **Gemini Developer API (default)**: Set `GEMINI_API_KEY` in the environment, or pass `api_key: ${GEMINI_API_KEY}` in YAML.
- **Vertex AI**: Set `vertex: true` (or `GOOGLE_GENAI_USE_VERTEXAI=true`) with either `project` + `location` (Standard Mode via Application Default Credentials) or `api_key` (Express Mode).
- **Local OpenAI-compatible servers** (Ollama, LM Studio, etc.): Set `openai_compatible: true`, `base_url`, and `model`.

## How it works

- Each agent manages a `google.antigravity.Agent` session backed by the bundled `localharness` binary.
- Streaming deltas (thinking and text), tool call lifecycle events (`ToolCallStartEvent` / `ToolCallProgressEvent` / `ToolCallCompleteEvent`), context compaction, and nested subagent activity (`SubAgentEvent`) are mapped directly into AgentPool's event stream.
- Tool confirmations (`policy_mode: confirm_run_command`, `safe_defaults`, or `auto`) and interactive questions (`behavior: interactive`) route through AgentPool's `InputProvider`.
- AgentPool `tools` and toolsets are exposed to Antigravity through an in-process Streamable HTTP MCP bridge (`ToolManagerBridge`), alongside any configured `mcp_servers`.
- Structured output (`output_type` / `to_structured()`) uses Antigravity's native `response_schema` and `finish` tool.
- Sessions are persisted under `save_dir` (defaulting to an AgentPool-managed data directory) so `load_session` and config option changes can resume existing conversations.

## Configuration Reference

/// mknodes
{{ "agentpool.models.antigravity_agents.AntigravityAgentConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False) }}
///

## Basic Usage

```yaml
agents:
  coder:
    type: antigravity
    model: gemini-4-hydrogen
    thinking_level: high
    api_key: ${GEMINI_API_KEY}
    policy_mode: confirm_run_command
    enable_sandbox: true
    system_prompt: "You are a careful Python engineer."
    tools:
      - type: subagent

  vertex_reviewer:
    type: antigravity
    vertex: true
    project: my-gcp-project
    location: global
    model: gemini-3.1-pro-preview
    policy_mode: safe_defaults
    disabled_tools:
      - generate_image
    budget:
      max_model_calls: 20
      max_total_tokens: 200000

  local_ollama:
    type: antigravity
    openai_compatible: true
    base_url: http://localhost:11434/v1
    model: qwen3:32b
    behavior: minimal
```

## Configuration Notes

- `policy_mode` defaults to `allow_all` for programmatic use; switch to `confirm_run_command`, `safe_defaults`, or `auto` to require interactive approvals through AgentPool's `InputProvider`.
- `mode` (policy preset), `thought_level`, `behavior`, and `model` are exposed as switchable config options and take effect on the next turn (resuming the current conversation when active).
- Setting `behavior: interactive` automatically enables Antigravity's `ask_question` tool (unless `enabled_tools` is explicitly specified) and bridges questions through `InputProvider.get_elicitation()`.
