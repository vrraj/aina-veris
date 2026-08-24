# Tool Registry

[← Documentation home](index.md)

`prompts/tool_registry.yaml` is the configuration surface for local, REST-backed,
and external MCP capabilities. It keeps tool schemas and policy outside API
routes and lets domains use a shared research pipeline with different tools.

## Tool types

- **Local tools** run in the Aina-Veris process, such as weather, nearby
  airports, and deterministic chart rendering.
- **REST-backed tools** normalize an external service response before it reaches
  inference.
- **MCP tools** are discovered from enabled servers and invoked through the MCP
  client.

## Add a tool

1. Add its schema, description, availability, and integration configuration to
   `prompts/tool_registry.yaml`.
2. Implement the executor outside API routes.
3. Expose it through the REST service when it is a reusable capability.
4. Add tests for stable behavior and output normalization.

Tool output can include compact data, citations, and configured deterministic
artifacts such as SVG charts. Raw visual payloads should not be placed directly
in model context.

See [MCP integration](mcp_specs.md) for external servers and server-side
research tools.
