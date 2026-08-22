import os

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.integrations.mcp.adapters import normalize_mcp_result


def test_tavily_adapter_normalizes_documented_result_sources_without_tool_name_matching():
    result = normalize_mcp_result(
        "tavily",
        "renamed_tavily_tool",
        {
            "content": [
                {
                    "type": "text",
                    "text": (
                        '{"results":[{"title":"Example",'
                        '"url":"https://example.com/article",'
                        '"content":"Source snippet"}]}'
                    ),
                }
            ]
        },
    )

    assert result is not None
    assert result.sources[0].as_dict() == {
        "title": "Example",
        "url": "https://example.com/article",
        "snippet": "Source snippet",
        "provider": "tavily",
    }


def test_unknown_integration_is_left_unnormalized():
    assert normalize_mcp_result("unknown", "some_tool", {"content": []}) is None
