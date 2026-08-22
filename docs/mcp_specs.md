# MCP (Model Context Protocol) Integration Specification

This document describes the Model Context Protocol (MCP) integration in the system, including tool discovery, resolution, execution, and artifact synthesis.

## Overview

The system integrates with external MCP servers to extend tool capabilities beyond local functions. MCP tools are discovered automatically, merged with registry definitions, and executed via JSON-RPC over HTTP. Structured JSON responses can be synthesized into artifacts (e.g., SVG sparklines) that are injected into the final LLM response.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Tool Registry                              │
│  (prompts/tool_registry.yaml)                                     │
│  • MCP server definitions (URL, transport)                       │
│  • Tool definitions (name, artifact config)                       │
│  • Runtime metadata (endpoint type, remote tool name)            │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     MCP Client Layer                               │
│  (backend/integrations/mcp/client.py)                             │
│  • Tool discovery (tools/list)                                    │
│  • Runtime merging (discovered + registry)                         │
│  • Tool execution (tools/call)                                     │
│  • Structured content extraction                                  │
│  • Artifact synthesis                                             │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     MCP Adapter Layer                             │
│  (backend/integrations/mcp/mcp_adapter.py)                        │
│  • HTTP JSON-RPC client                                          │
│  • tools/list endpoint                                            │
│  • tools/call endpoint                                            │
│  • Error handling                                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                   External MCP Servers                            │
│  • Agis Markets (http://localhost:9001/mcp)                        │
│  • Other MCP servers (configurable)                               │
└─────────────────────────────────────────────────────────────────┘
```

## Tool Discovery

### 1. Server Configuration

MCP servers are defined in `prompts/tool_registry.yaml` under the `mcp_servers` section:

```yaml
mcp_servers:
  agis-markets:
    enabled: true
    transport: streamable_http
    url: http://localhost:9001/mcp
```

**Fields:**
- `enabled`: Whether the server is active
- `transport`: Transport type (currently only `streamable_http` is supported)
- `url`: MCP server endpoint URL

### 2. Tool Discovery Process

The discovery process is initiated by `get_mcp_tool_definitions()` in `backend/integrations/mcp/client.py`:

1. **Load enabled servers**: Filter `mcp_servers` for `enabled: true`
2. **Validate transport**: Ensure `transport == "streamable_http"`
3. **Call tools/list**: For each server, send JSON-RPC request:
   ```json
   {
     "jsonrpc": "2.0",
     "id": "<uuid>",
     "method": "tools/list",
     "params": {}
   }
   ```
4. **Parse response**: Extract tool definitions (name, description, input_schema)
5. **Build runtime metadata**: Attach server name and URL to each tool
6. **Cache results**: Cache definitions with TTL (default 300 seconds)

**Implementation:** `backend/integrations/mcp/client.py::_discover_enabled_servers()`

### 3. Discovered Tool Structure

Each discovered tool has the following structure:

```python
{
    "name": "get_stock_price_history",
    "description": "Fetch historical stock price data",
    "input_schema": {
        "type": "object",
        "properties": {
            "symbols": {"type": "array", "items": {"type": "string"}},
            "period": {"type": "string"}
        }
    },
    "runtime": {
        "mcp_server": "agis-markets",
        "mcp_url": "http://localhost:9001/mcp"
    }
}
```

## Runtime Merging

### 1. Registry Tool Definitions

Tools can be pre-defined in `prompts/tool_registry.yaml` with artifact configuration:

```yaml
tools:
  get_stock_price_history:
    enabled: true
    endpoint:
      type: mcp
      url: http://localhost:9001/mcp
      tool: get_stock_price_history
    produces_artifact: true
    artifact_type: svg
    artifact_key: svg
    placeholder: "{{ARTIFACT:stock_chart_svg}}"
```

**Fields:**
- `endpoint.type`: Must be `mcp` for MCP tools
- `endpoint.url`: MCP server URL
- `endpoint.tool`: Remote tool name on the MCP server
- `produces_artifact`: Whether the tool generates artifacts
- `artifact_type`: Type of artifact (svg, image, etc.)
- `artifact_key`: Key for artifact payload
- `placeholder`: Placeholder token for artifact injection

### 2. Merging Process

When a tool is discovered from an MCP server, the system merges it with any registry definition:

1. **Load registry tools**: Parse `prompts/tool_registry.yaml`
2. **Match by name**: Find registry entry matching discovered tool name
3. **Merge runtime metadata**: Combine discovered runtime with registry endpoint config
4. **Preserve artifact config**: Keep artifact metadata from registry
5. **Register merged runtime**: Store in `_MCP_RUNTIME_BY_TOOL` for execution

**Implementation:** `backend/integrations/mcp/client.py::_merge_runtime()`

### 3. Merged Runtime Structure

The merged runtime includes both discovery and registry information:

```python
{
    "mcp_server": "agis-markets",
    "mcp_url": "http://localhost:9001/mcp",
    "endpoint": {
        "type": "mcp",
        "url": "http://localhost:9001/mcp",
        "tool": "get_stock_price_history"
    },
    "artifact": {
        "produces_artifact": true,
        "artifact_type": "svg",
        "artifact_key": "svg",
        "placeholder": "{{ARTIFACT:stock_chart_svg}}"
    }
}
```

## Tool Execution

### 1. Execution Flow

When a tool is invoked during the pipeline:

1. **Tool resolution**: `get_executor()` in `backend/tools/__init__.py` detects MCP tools
2. **Runtime retrieval**: Fetch merged runtime from `_MCP_RUNTIME_BY_TOOL`
3. **Executor selection**: Use MCP executor (`backend/integrations/mcp/executor.py`)
4. **Synchronous call**: `call_mcp_tool_sync()` wraps async MCP client
5. **JSON-RPC request**: Send to MCP server via adapter
6. **Response processing**: Extract structured content, synthesize artifacts
7. **Artifact injection**: Replace placeholder with actual artifact

### 2. JSON-RPC Request

The MCP adapter sends a JSON-RPC request to the MCP server:

```json
{
    "jsonrpc": "2.0",
    "id": "<uuid>",
    "method": "tools/call",
    "params": {
        "name": "get_stock_price_history",
        "arguments": {
            "symbols": ["NVDA"],
            "period": "6M"
        }
    }
}
```

**Headers:**
- `Content-Type: application/json`
- `Accept: application/json, text/event-stream`

**Implementation:** `backend/integrations/mcp/mcp_adapter.py::adapter_call_tool()`

### 3. Response Handling

The MCP server returns a JSON-RPC response:

```json
{
    "jsonrpc": "2.0",
    "id": "<uuid>",
    "result": {
        "content": [
            {
                "type": "text",
                "text": "Stock price history for NVDA"
            }
        ],
        "structuredContent": [
            {
                "type": "json",
                "priceHistory": {
                    "success": true,
                    "symbols": ["NVDA"],
                    "period": "6M",
                    "items": [
                        {
                            "symbol": "NVDA",
                            "history": [
                                {"d": "2025-12-10", "c": 183.78},
                                {"d": "2025-12-11", "c": 180.93}
                            ]
                        }
                    ]
                }
            }
        ]
    }
}
```

**Response Processing Rules:**
1. If `structuredContent` exists and `type == "json"`, extract structured data
2. If `structuredContent` contains `priceHistory`, route to artifact synthesis
3. Otherwise, fall back to `content[].text` for plain text responses

**Implementation:** `backend/integrations/mcp/client.py::call_mcp_tool()`

## Artifact Synthesis

### 1. Price History Extraction

For tools that return price history data, the system extracts the raw `priceHistory` payload:

```python
def extract_price_history_from_mcp_response(structured_content: Any) -> Dict[str, Any]:
    """Return the priceHistory blob exactly as provided in structuredContent."""
    # Walk structuredContent (list or dict) for type: "json" entries
    # Extract priceHistory field
    # Validate success flag and items list
    # Return raw priceHistory dict
```

**Implementation:** `backend/integrations/mcp/price_history.py::extract_price_history_from_mcp_response()`

### 2. SVG Generation

The extracted price history is converted to an SVG sparkline:

```python
def synthesize_price_history_svg(extracted_data: Dict[str, Any], symbol: str) -> Tuple[str, Dict[str, Any]]:
    """Generate SVG from extracted MCP price history data."""
    # Convert history entries to renderer-friendly dicts: {"d": date, "c": close}
    # Call generate_timeseries_sparklines() with points, period, title
    # Return SVG string and metadata
```

**Implementation:** `backend/integrations/mcp/price_history.py::synthesize_price_history_svg()`

### 3. Artifact Output Building

The artifact output includes the placeholder and metadata:

```python
def build_artifact_output(svg: str, metadata: Dict[str, Any], tool_name: str, *, placeholder: str | None = None) -> Dict[str, Any]:
    """Build tool output with artifact placeholder for synthesis."""
    resolved_placeholder = placeholder or f"{{{{ARTIFACT:{tool_name}_svg}}}}"
    return {
        "summary": f"Stock chart generated for {symbol} ({period}) with {data_points} data points.",
        "artifact_placeholder": resolved_placeholder,
        "artifact_payload_omitted": True,
        "symbol": symbol,
        "period": period,
    }
```

**Implementation:** `backend/integrations/mcp/price_history.py::build_artifact_output()`

### 4. Artifact Injection

The artifact placeholder is carried through the pipeline and replaced in the final response:

1. **Tool execution stage**: Extracts `artifact_placeholder` and `_svg_artifact` from tool output
2. **Pipeline result**: Includes `artifacts` list in `ToolExecutionStageResult`
3. **Final response stage**: Calls `_inject_registered_artifacts()` to replace placeholders
4. **Replacement**: `{{ARTIFACT:stock_chart_svg}}` → actual SVG string

**Implementation:**
- `backend/chat/pipeline/stages/tool_execution.py::run_tool_execution_stage()`
- `backend/chat/pipeline/stages/final_response.py::run_final_response_stage()`
- `backend/chat/pipeline/tools.py::_inject_registered_artifacts()`

## Configuration

### Tool Registry

The tool registry (`prompts/tool_registry.yaml`) is the central configuration for MCP tools:

```yaml
mcp_servers:
  agis-markets:
    enabled: true
    transport: streamable_http
    url: http://localhost:9001/mcp

tools:
  get_stock_price_history:
    enabled: true
    endpoint:
      type: mcp
      url: http://localhost:9001/mcp
      tool: get_stock_price_history
    produces_artifact: true
    artifact_type: svg
    artifact_key: svg
    placeholder: "{{ARTIFACT:stock_chart_svg}}"
```

### Environment Variables

MCP configuration can be overridden via environment variables:

- `MCP_TOOLS_REFRESH`: TTL for tool definition cache (default: 300 seconds)
- MCP server URLs are configured in `prompts/tool_registry.yaml`

## Error Handling

### MCP Adapter Errors

The MCP adapter raises `MCPAdapterError` for HTTP or JSON-RPC errors:

```python
class MCPAdapterError(RuntimeError):
    """Raised when an MCP HTTP call fails."""
```

**Error handling:**
- HTTP status errors are logged and re-raised
- JSON-RPC errors are unwrapped and logged
- Timeouts are configurable (default: 30 seconds)

### Tool Execution Errors

Tool execution errors are caught and logged:

```python
try:
    result = await adapter_call_tool(adapter_url, target_tool_name, args)
except MCPAdapterError as exc:
    logger.error("[MCP] Failed to call tool '%s' on server '%s': %s", ...)
    raise
```

### Artifact Synthesis Errors

Artifact synthesis failures are logged but do not fail the tool call:

```python
if not svg or "<svg" not in svg.lower():
    logger.warning("[MCP_PRICE_HISTORY] SVG generation failed for symbol=%s", symbol)
    return "", {}
```

## Logging

MCP operations are logged with the `[MCP]` prefix:

- Tool discovery: `[MCP] Discovered N tools from server 'name'`
- Tool execution: `[MCP] Calling tool 'name' on server 'name'`
- Artifact synthesis: `[MCP] Synthesized SVG for tool='name' symbol='X' data_points=N`
- Errors: `[MCP] Failed to call tool 'name' on server 'name': error`

SSE stages are prefixed with `mcp:` for MCP tools (e.g., `Calling Tool: mcp:get_stock_price_history`).

## Example: Stock Price History Tool

### Registry Definition

```yaml
tools:
  get_stock_price_history:
    enabled: true
    endpoint:
      type: mcp
      url: http://localhost:9001/mcp
      tool: get_stock_price_history
    produces_artifact: true
    artifact_type: svg
    artifact_key: svg
    placeholder: "{{ARTIFACT:stock_chart_svg}}"
```

### Execution Flow

1. **Discovery**: System calls `tools/list` on `http://localhost:9001/mcp`
2. **Merging**: Discovered tool merged with registry artifact config
3. **Execution**: LLM requests `get_stock_price_history` with `symbols=["NVDA"], period="6M"`
4. **JSON-RPC**: Server returns structured `priceHistory` data
5. **Extraction**: `extract_price_history_from_mcp_response()` extracts raw payload
6. **Synthesis**: `synthesize_price_history_svg()` generates SVG sparkline
7. **Placeholder**: Tool output includes `artifact_placeholder: "{{ARTIFACT:stock_chart_svg}}"`
8. **Injection**: Final response replaces placeholder with actual SVG

### Tool Output

```python
{
    "summary": "Stock chart generated for NVDA (6M period) with 126 data points.",
    "artifact_placeholder": "{{ARTIFACT:stock_chart_svg}}",
    "artifact_payload_omitted": True,
    "symbol": "NVDA",
    "period": "6M",
    "_svg_artifact": "<svg>...</svg>"
}
```

### Final Response

The LLM response includes the placeholder, which is replaced before delivery to the client:

```
Here is the stock price history for NVDA over the past 6 months:

{{ARTIFACT:stock_chart_svg}}

The stock has trended upward from $180.93 to $183.78 over this period.
```

After injection:

```
Here is the stock price history for NVDA over the past 6 months:

<svg>...</svg>

The stock has trended upward from $180.93 to $183.78 over this period.
```

## Security Considerations

1. **Server URLs**: MCP server URLs should be trusted and validated
2. **Input validation**: Tool arguments are validated against input schemas
3. **Artifact sanitization**: SVG content should be sanitized before injection
4. **Access control**: MCP tools respect domain-based access controls
5. **Rate limiting**: MCP server calls should be rate-limited to prevent abuse

## Future Enhancements

1. **Additional transports**: Support for WebSocket and SSE transports
2. **Streaming responses**: Support for streaming tool outputs
3. **Multiple artifacts**: Support for multiple artifacts per tool
4. **Artifact caching**: Cache generated artifacts to reduce recomputation
5. **Tool versioning**: Support for versioned tool definitions
6. **Server authentication**: Add authentication for MCP servers
