# Embeddable Chat

[← Documentation home](index.md)

`frontend/chat-embed.html` is a small client over the same `POST /chat` API.
It can be embedded directly in an iframe or loaded into a host page.

```html
<div id="support-chat"></div>
<script
  src="https://veris.example/static/embed-loader.js"
  data-target="#support-chat"
  data-active_domain="finance"
  data-top_k="5"
  data-height="450px"
></script>
```

The loader passes other `data-*` values as embed query parameters. The host
application is responsible for identity, authorization, framing policy, Origin
policy, and content-security policy when embedding chat publicly.

See [API surfaces](api-reference.md) and [Security](security.md).
