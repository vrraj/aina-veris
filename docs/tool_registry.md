---
layout: default
title: "Tool Registry Guide"
description: "Configure tools and safely inject tool-produced artifacts into Aina Veris responses."
---

# Tool Registry Guide

The Tool Registry controls which tools are available to the chat pipeline and, when applicable, how a tool-produced visual artifact is added to the final response. The editor at `/tool-registry` saves changes to `prompts/tool_registry.yaml` and creates a `.bak` backup before replacing it.

## Everyday workflow

1. Select a tool and enable or disable it.
2. For a normal data tool, leave **Produces Artifact** disabled.
3. For a tool that returns a visual payload, enable **Produces Artifact** and complete the artifact contract.
4. Select **Save Changes**. The editor validates the registry, writes the YAML, and clears its cache.
5. Use **Reload** to discard unsaved edits and read the current file. Use **Reload Cache** when the YAML was edited outside the editor.

Changes take effect for the next tool synthesis request; they do not modify a response already in progress.

## What an artifact is

An artifact is a displayable payload returned by a tool, currently most commonly an SVG chart. It is deliberately kept out of the language-model synthesis input. Instead, the system validates and sanitizes it, then inserts it into the final response.

The flow is:

`Tool response` → `extract payload by artifact_key` → `validate policy and sanitize SVG` → `replace placeholder or append verbatim` → `final response`

For registry-configured artifacts, injection occurs only when all of these conditions are met:

- Artifact injection is enabled.
- The tool is listed in **Allowed Artifact Tools** (or the list is intentionally empty).
- The tool’s artifact contract says it produces an artifact.
- Its type and injection mode are allowed by the global policy.
- The payload is within the configured size limit and passes SVG safety checks.

## Artifact contract fields

| Field | Meaning | Example |
| --- | --- | --- |
| Produces Artifact | Marks a tool as returning a displayable payload. | `true` |
| Artifact Type | The payload format. It must be globally allowed. | `svg` |
| Artifact Key | The field in the tool’s JSON output containing the payload. | `svg` |
| Injection Mode | How the payload is added to the response. | `verbatim` |
| Placeholder Token | The token the response uses to place the artifact. | `{{ARTIFACT:stock_chart_svg}}` |

With the contract above, an output such as `{"svg": "<svg>…</svg>"}` supplies the payload. If the generated answer includes `{{ARTIFACT:stock_chart_svg}}`, the final response replaces that token with the sanitized SVG. With `verbatim`, the system appends the payload when no placeholder is present.

## Global artifact policy

The policy applies to every artifact-producing tool:

- **Artifact Injection Enabled**: master switch for all artifact insertion.
- **Allowed Artifact Tools**: tool-name allowlist. Keep this limited to tools you trust to return visual output.
- **Max Artifact Chars**: maximum size for one payload. Larger payloads are ignored.
- **Allowed Artifact Types**: accepted formats. The current safe default is `svg`.
- **Allowed Injection Modes**: accepted placement behaviors. The current safe default is `verbatim`.
- **Enforce Placeholder Format**: requires `{{ARTIFACT:name}}`, where `name` uses letters, numbers, dots, underscores, colons, or hyphens.

## Example

```yaml
artifact_injection:
  enabled: true
  allowed_tools:
    - get_stock_price_history
  security:
    max_artifact_chars: 120000
    allowed_artifact_types: [svg]
    allowed_injection_modes: [verbatim]
    enforce_placeholder_format: true

tools:
  - name: get_stock_price_history
    enabled: true
    runtime:
      endpoint:
        type: mcp
        url: http://host.docker.internal:9001/mcp
        tool: get_stock_price_history
    artifact:
      produces_artifact: true
      artifact_type: svg
      artifact_key: svg
      injection_mode: verbatim
      placeholder: "{{ARTIFACT:stock_chart_svg}}"
```

## Adding and managing tools

Each tool needs a unique `name`, an `enabled` flag, and a `runtime.endpoint` with a `type` and `url`. MCP tools additionally identify the remote tool with `runtime.endpoint.tool`. Keep runtime and connection settings in the YAML/configuration; do not place credentials in this file.

The editor currently manages enabled status and artifact metadata. Add or change a tool’s runtime endpoint directly in `prompts/tool_registry.yaml`, then use **Reload Cache** (or restart the application) before testing it.

## Safety notes

Treat every artifact-producing tool as a trusted integration. SVG payloads are size-limited, reject known active-content patterns, and are sanitized before insertion. Keep the allowed tool and artifact-type lists narrow, use distinct placeholders, and test a new tool with a small payload before enabling it in production.
