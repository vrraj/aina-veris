import os

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.integrations.mcp.client import resolve_mcp_server_url
from backend.integrations.mcp.mcp_adapter import MCPAdapterError, _decode_response, _post_jsonrpc


def test_resolve_mcp_server_url_injects_query_parameter_from_environment(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "secret value")

    url = resolve_mcp_server_url(
        "tavily-search",
        {
            "url": "https://mcp.tavily.com/mcp?existing=1",
            "auth": {
                "type": "query_parameter",
                "parameter": "tavilyApiKey",
                "env": "TAVILY_API_KEY",
            },
        },
    )

    assert url == "https://mcp.tavily.com/mcp?existing=1&tavilyApiKey=secret+value"


def test_resolve_mcp_server_url_requires_configured_environment_variable(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    try:
        resolve_mcp_server_url(
            "tavily-search",
            {
                "url": "https://mcp.tavily.com/mcp",
                "auth": {
                    "type": "query_parameter",
                    "parameter": "tavilyApiKey",
                    "env": "TAVILY_API_KEY",
                },
            },
        )
    except ValueError as exc:
        assert "TAVILY_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected missing MCP auth environment variable to fail")


def test_mcp_http_errors_do_not_include_query_credentials(monkeypatch):
    class Response:
        status_code = 400

        def raise_for_status(self):
            import httpx

            request = httpx.Request("POST", "https://mcp.example/mcp?tavilyApiKey=secret")
            raise httpx.HTTPStatusError("sensitive request URL", request=request, response=self)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("backend.integrations.mcp.mcp_adapter.httpx.AsyncClient", lambda **kwargs: Client())

    import asyncio

    try:
        asyncio.run(_post_jsonrpc("https://mcp.example/mcp?tavilyApiKey=secret", {}))
    except MCPAdapterError as exc:
        assert str(exc) == "MCP server returned HTTP 400"
        assert "secret" not in str(exc)
    else:
        raise AssertionError("Expected MCP HTTP failure")


def test_mcp_adapter_decodes_jsonrpc_message_from_sse_response():
    import httpx

    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=b'event: message\ndata: {"jsonrpc":"2.0","result":{"tools":[]}}\n\n',
    )

    assert _decode_response(response, "https://mcp.example/mcp") == {
        "jsonrpc": "2.0",
        "result": {"tools": []},
    }
