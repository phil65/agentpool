---
title: MCP Sampling & Elicitation
description: MCP server with sampling tools
icon: material/lightning-bolt
hide:
  - toc
---

/// mknodes
{{ ['docs/examples/mcp_sampling_elicitation/demo.py', 'docs/examples/mcp_sampling_elicitation/server.py'] | pydantic_playground }}
///

Example
This example demonstrates how to create and use a FastMCP server that combines **sampling** and **elicitation** in a single workflow.

## Overview

The example consists of:

## The Code Fixer Tool

A single tool that demonstrates both MCP capabilities in one workflow:

1. **Sampling** (Server-side LLM): Analyzes the provided code for syntax errors, style issues, and improvements
2. **Elicitation** (Direct user interaction): Asks the user whether to proceed with fixing the identified issues  
3. **Sampling** (Server-side LLM): Generates the corrected code based on the analysis and user approval

**Input**: Code string (e.g., `print("hello world")` with typo)
**Output**: Analysis results and fixed code (if approved)

## Key Patterns

- **Server autonomy**: The server orchestrates a complex multi-step workflow internally
- **Direct user interaction**: Server asks user for decisions without going through the agent
- **Server-side intelligence**: Uses the client's LLM for both analysis and code generation
- **Single tool interface**: Agent sees one simple tool, server handles complexity
- **Multi Round-Trip Requests**: Since MCP `2026-07-28`, servers can't call back into the client
  mid-call. The tool returns an `InputRequiredResult`, the client answers the embedded requests
  and retries the call, and `request_state` tells the tool which step it is in.
  AgentPool drives these retries automatically.

!!! warning
    Sampling is deprecated as of MCP `2026-07-28`
    ([SEP-2577](https://modelcontextprotocol.io/seps/2577-deprecate-roots-sampling-and-logging))
    and may be removed in the first spec revision released on or after 2027-07-28.
    Elicitation is not deprecated.

## Running the Example

The demo shows a complete workflow: code analysis → user confirmation → code fixing, all within one tool call.
