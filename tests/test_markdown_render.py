from backend.markdown_render import render_markdown_to_html


def test_sources_render_each_tool_citation_on_its_own_line():
    html = render_markdown_to_html(
        "Sources:\n"
        "[1] https://example.com/nvidia\n"
        "[tool-1] https://example.com/analyst-one\n"
        "[tool-3] https://example.com/analyst-two\n"
        "[tool-4] https://example.com/analyst-three"
    )

    assert html.count("<br/>") == 3
    assert "[tool-1] https://example.com/analyst-one<br/>" in html
    assert "[tool-3] https://example.com/analyst-two<br/>" in html
