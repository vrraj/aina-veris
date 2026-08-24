# Deployment

[← Documentation home](index.md)

The supplied `docker-compose.yml` is a local-development configuration: it uses
`run.py` with reload, bind-mounts the repository, and publishes the application
and Qdrant ports. Do not treat it as a public-production topology.

## Production boundary

Run the application behind infrastructure that provides:

- HTTPS termination and network boundaries;
- authentication and authorization for public REST, A2A, MCP, ingestion, and
  embedded-chat interfaces;
- rate limits, request-size limits, audit logging, and egress policy;
- restricted access to OpenAPI and debug/admin routes;
- private or localhost-only Qdrant networking.

Qdrant data is stored in the `qdrant_storage/` bind mount in the supplied
Compose configuration. Back it up before changing host storage or upgrading
infrastructure. Do not use `docker compose down -v` as part of a data migration.

## Secrets and configuration

Supply provider keys and tool credentials through environment variables or a
secret manager. Use `.env.example` only as a template; `.env` must remain
untracked. Configure a public A2A base URL through
`A2A_VERIS_PUBLIC_BASE_URL` when publishing AgentCards.

For the full protocol and authorization boundary, see [Security](security.md).
