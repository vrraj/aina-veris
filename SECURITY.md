# Security

Aina-Veris is a reference framework for domain-aware RAG research and
agent integration. It can be deployed locally, within a private network,
or behind application and API infrastructure.

Aina-Veris does not include a built-in identity provider, user store, or
tenant authorization model. Authentication and authorization are
therefore deployment concerns rather than framework-provided
capabilities.

## Deployment Recommendations

For deployments that expose REST, A2A, MCP, or ingestion endpoints
outside a trusted environment, consider controls appropriate to the
deployment, such as:

-   authentication and authorization
-   API gateways or OAuth/OIDC providers
-   service-to-service tokens
-   private network boundaries
-   HTTPS
-   rate limiting
-   access and audit logging

Origin and Host allowlists can provide useful browser and deployment
controls, but they are not authentication or authorization mechanisms.

## Public Interfaces

| Surface | Deployment controls |
|---|---|
| REST chat, search, and sessions | Authenticate callers; authorize the requested domain and operation; apply rate limits and audit logging. |
| A2A research agents | Authenticate the calling service; authorize access to the named agent/domain; apply quotas, request limits, and audit logging. |
| MCP over HTTP | Use MCP-compatible authentication, validate Origin, enforce tool/domain access, apply rate limits, and audit calls. |
| Ingestion | Authenticate callers; authorize the target domain; restrict uploads and fetched URLs; apply size, source, and rate limits; audit indexing activity. |
| SSE stage streams | Authorize the request or session that owns the `query_id`; do not expose another caller's stream events. |
| Embedded chat | Use deployment-specific identity or an intentionally controlled public access model; set framing, Origin, and content-security policy. |

## MCP Deployment

Aina-Veris can expose domain-scoped research capabilities through MCP.
The `/mcp` endpoint ships without authentication for local development and
trusted-network use. Internet-facing MCP deployments should put an
authentication and authorization layer in front of it.

For a public or marketplace-connected Streamable HTTP endpoint, deploy
Aina-Veris as an OAuth 2.1 protected resource backed by an OIDC-compatible
authorization server. The layer in front of Aina-Veris should validate bearer
tokens, including their signature, issuer, audience, expiry, and an appropriate
research scope. It should return `401` for missing or invalid credentials and
`403` for a valid token without the required scope.

Tokens belong in the `Authorization` header, never in a query string, URL path,
or tool argument. An OAuth-protected MCP deployment should expose Protected
Resource Metadata at:

```text
/.well-known/oauth-protected-resource/mcp
```

That metadata identifies the canonical MCP resource URI, supported scopes, and
the authorization server. A `401` response from `/mcp` should include a
`WWW-Authenticate: Bearer` challenge with the `resource_metadata` URL.

For private machine-to-machine integrations, a gateway may validate a static
bearer token or API key before proxying to `/mcp`. Store and rotate that
credential through a secrets manager. This is suitable for controlled internal
integrations, not the OAuth discovery flow expected by general remote MCP
clients and marketplaces.

The stdio MCP server is launched by its host application and does not use the
HTTP OAuth flow. The host process is responsible for access controls and for
supplying credentials through its environment or secret manager.

## Tool and Model Controls

Tool calls and model inference can incur cost or reach external systems. Public
deployments should apply per-caller quotas and audit trails, limit tool access
by caller and domain where required, and keep provider credentials out of
registries and client code.

The A2A limits in the application bound prompt size, retrieval results, output
tokens, concurrency, and tool calls. They do not replace authentication,
authorization, rate limits, or tenant isolation.

## Secrets and Credentials

API keys, MCP credentials, provider tokens, and other secrets should be
supplied through environment variables or an appropriate
secrets-management mechanism rather than committed to the repository.

## Scope

This document describes the security boundary of the Aina-Veris
reference framework and provides deployment recommendations. It does not
represent a security certification, independent audit,
production-hardening claim, or commitment to provide security
monitoring, vulnerability response, or remediation.

## References

- [MCP Authorization Specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [OAuth 2.0 Protected Resource Metadata (RFC 9728)](https://www.rfc-editor.org/rfc/rfc9728)
