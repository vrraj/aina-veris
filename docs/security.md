# Security

[← Documentation home](index.md)

Aina-Veris is a reference framework. It does not include a built-in identity
provider, user store, tenant authorization model, or public-endpoint rate
limiter.

Deployments that expose REST, A2A, MCP, ingestion, SSE, or embedded chat outside
a trusted network should provide authentication, authorization, HTTPS, rate
limits, audit logging, and appropriate network and Origin policy at the
deployment boundary.

MCP over HTTP requires an MCP-compatible authentication layer for public use.
Ingestion must authorize the target domain and constrain uploads and fetched
URLs. SSE streams must be scoped to the request or session that owns the
`query_id`.

Read the complete [repository security policy](https://github.com/vrraj/aina-veris/blob/main/SECURITY.md) before deploying publicly.
