from backend.chat.pipeline.context import format_context_lines


def test_inference_context_preserves_document_metadata():
    context = format_context_lines(
        [
            {
                "payload": {
                    "base_url": "file://ANM-D5C-0001_datasheet.pdf",
                    "url": "file://ANM-D5C-0001_datasheet.pdf#page=2",
                    "title": "ANM-D5C-0001 DDR5 SDRAM",
                    "section": "Electrical Characteristics",
                    "subsection": "Standby Current",
                    "text": "IDD2N is specified for the supported operating range.",
                }
            }
        ]
    )

    assert context == (
        "[1]\n"
        "Source: file://ANM-D5C-0001_datasheet.pdf\n"
        "Document title: ANM-D5C-0001 DDR5 SDRAM\n"
        "Section: Electrical Characteristics > Standby Current\n\n"
        "IDD2N is specified for the supported operating range."
    )


def test_inference_context_falls_back_from_base_url_to_url_and_source():
    context = format_context_lines(
        [
            {
                "payload": {
                    "url": "https://example.com/note",
                    "source": "https://fallback.example.com/note",
                    "section": "Overview",
                    "text": "Application note summary.",
                }
            }
        ]
    )

    assert "Source: https://example.com/note" in context
    assert "Document title: Untitled document" in context
    assert "Section: Overview" in context
