## Upstream Schema Sync

The models in `acp.schema` are hand-written against a pinned release of the upstream
[ACP schema](https://github.com/agentclientprotocol/agent-client-protocol/tree/main/schema).
Each protocol version has a pin in `schema/acp/v{N}/`:

| File | Content |
| --- | --- |
| `VERSION` | Upstream git tag, e.g. `schema-v1.33.0` |
| `schema.unstable.json`, `meta.unstable.json` | Upstream files of that tag, unchanged |
| `conformance.toml` | Renames and known differences, each with a reason |

```bash
uv run python -m acp.schema_reference latest            # pinned vs newest upstream tag
uv run python -m acp.schema_reference sync --dry-run    # changelog of the newest tag
uv run python -m acp.schema_reference sync              # pin the newest tag
uv run python -m acp.schema_reference check             # models vs pinned schema
```

Use `--protocol 2` for the v2 pin.

`schema/acp/references.toml` additionally pins reference implementations by commit
(the official TypeScript SDK and claude-agent-acp), built on the same schema releases.
`uv run python -m acp.schema_reference refs` lists the upstream commits since each pin
that touched the watched paths, with a compare link; `refs --update NAME` moves a pin.
Set `GITHUB_TOKEN` to avoid GitHub's anonymous rate limit.

## Draft ACP v2

v2 is prepared next to v1 but not used yet:

- `acp.schema.v2`: the v2 models (optional fields default to `MISSING` and are left
  out, `None` is sent as `null`; serialize with `to_wire`, test fields with `is_set`).
- `acp.v2.agent` / `acp.v2.client`: `Agent` / `Client` base classes (unimplemented
  methods answer `-32601`) and their connections, which enforce the v2 rules
  (initialize first, no repeated initialize after other requests, string/integer
  request IDs, no batches).
- `acp.router.RoutedAgentConnection`: serves a client with the v1 or the v2 agent,
  selected by the `initialize` request, like the TypeScript SDK's router. To sync, run `sync`, apply the printed changelog to
the models by hand, and run `check` until it reports no undocumented differences.
`tests/acp/test_schema_reference.py` runs the same check, so drift fails CI; a known
difference that no longer occurs fails it too, so remove fixed entries from
`conformance.toml`.

## ACP Protocol Features

### JSON-RPC 2.0 Communication

ACP uses JSON-RPC 2.0 over newline-delimited JSON streams:

```json
// Initialize request
{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":1},"id":1}

// Initialize response
{"jsonrpc":"2.0","result":{"protocolVersion":1,"agentCapabilities":{...}},"id":1}

// Create session
{"jsonrpc":"2.0","method":"session/new","params":{"cwd":"/tmp"},"id":2}

// Session response
{"jsonrpc":"2.0","result":{"sessionId":"sess_abc123"},"id":2}

// Send prompt
{"jsonrpc":"2.0","method":"session/prompt","params":{
  "sessionId":"sess_abc123",
  "prompt":[{"type":"text","text":"Hello!"}]
},"id":3}

// Streaming responses via session updates
{"jsonrpc":"2.0","method":"session/update","params":{
  "sessionId":"sess_abc123",
  "update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"Hi there!"}}
}}
```

### Content Blocks

ACP supports rich content blocks:

```python
# Text content
TextContent(text="Hello world")

# Image content
ImageContent(type="image", data="base64...", mimeType="image/png")

# Audio content
AudioContent(type="audio", data="base64...", mimeType="audio/wav")

# Resource links
ResourceContentBlock(type="resource_link", uri="file:///path/to/file", name="document.pdf")

# Embedded resources
EmbeddedResource(type="resource", resource=TextResourceContents(uri="...", text="..."))
```


### MCP Server Integration

ACP provides seamless integration with MCP (Model Context Protocol) servers, allowing agents to access external tools and data sources. MCP servers are automatically connected when creating sessions.

#### How MCP Integration Works

1. **Session Creation**: Client provides MCP server configurations in `session/new`
2. **Automatic Connection**: ACP server connects to all specified MCP servers
3. **Tool Integration**: MCP tools become available to the agent automatically
4. **Transparent Usage**: Agent can use MCP tools just like built-in tools
