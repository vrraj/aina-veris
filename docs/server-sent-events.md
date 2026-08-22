# Server-Sent Events

[← Documentation home](index.md)

Aina-Veris emits stage-level execution updates for a request `query_id`:

```text
GET /chat/stream/stages?query_id=<query-id>
```

Events expose major research stages such as turn resolution, retrieval,
reranking, context assembly, tool calls, inference, and response completion.
They also provide keepalives and support stage-level latency, token, and cost
visibility.

Browser clients should use `EventSource`, handle reconnection, and treat the
stream as observability data rather than an authorization mechanism. A public
deployment must authorize the request or session that owns the `query_id`; do
not allow one caller to subscribe to another caller's stream. See
[Security](security.md).
