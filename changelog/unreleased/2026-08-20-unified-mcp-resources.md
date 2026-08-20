# Add unified progressive MCP resource reads

AgentPool now exposes exactly three model-facing MCP resource tools:
`list_mcp_resources`, `list_mcp_resource_templates`, and
`read_mcp_resource`. Lists preserve server attribution and upstream MCP
fields, support bounded opaque pagination across servers, and return partial
results with structured errors when one provider fails. Reads route by the
exact `(scope, server, uri)` identity, preserve text content, and attach only
supported PDF/image blobs within the 10 MB limit.

Resource-capable servers are registered after MCP capability negotiation,
including servers whose current resource list is empty. Servers that expose
tools but do not advertise `resources` remain connected for tools without
entering the resource registry.

The OpenCode Host path remains independent of the model-tool gate:
`resources.enabled: false` hides the three tools but does not disable
`/experimental/resource`, provider lifecycle, or valid `FilePart` /
`ResourceSource` injection. Catalog and injection routing now preserve the
configured server name, so two servers may safely expose the same URI.
