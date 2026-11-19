---
title: MCP Elicitation
description: MCP server asking the user for input
icon: material/lightning-bolt
hide:
  - toc
---

/// mknodes
{{ ['docs/examples/mcp_elicitation/demo.py', 'docs/examples/mcp_elicitation/server.py'] | pydantic_playground }}
///

This example shows a FastMCP server that asks the user for input in the middle of a tool call,
using **elicitation**.

## The Release Tool

One tool, three steps:

1. **Choice**: asks which part of the version to bump (`patch`, `minor` or `major`)
2. **Confirmation**: asks whether to release the resulting version
3. **Result**: reports the release, or that it was cancelled

## Key Patterns

- **Direct user interaction**: the server asks the user for decisions without going through the agent
- **Structured answers**: each request carries a JSON schema (an enum, a boolean), so the client
  can render a proper form
- **Single tool interface**: the agent sees one simple tool, the server handles the workflow
- **Multi Round-Trip Requests**: since MCP `2026-07-28`, servers can't call back into the client
  mid-call. The tool returns an `InputRequiredResult`, the client answers the embedded requests
  and retries the call, and `request_state` tells the tool which step it is in.
  AgentPool drives these retries automatically.

!!! note
    AgentPool does not support MCP sampling (servers requesting LLM completions from the client).
    Sampling is deprecated as of MCP `2026-07-28`
    ([SEP-2577](https://modelcontextprotocol.io/seps/2577-deprecate-roots-sampling-and-logging));
    servers that need an LLM should call a provider directly.
