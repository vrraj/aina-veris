from backend.chat.pipeline.tools import _extract_artifacts_from_tool_outputs
from backend.chat.pipeline.tools import _redact_tool_outputs_for_synth


def test_artifact_synthesis_input_omits_chart_series_and_keeps_summary():
    outputs = [
        {
            "name": "get_stock_price_history",
            "output": (
                '{"summary":"Stock chart generated for NVDA.",'
                '"structured_payload":{"priceHistory":{"history":[{"c":1}]}},'
                '"artifact_placeholder":"{{ARTIFACT:stock_chart_svg}}"}'
            ),
            "_svg_artifact": "<svg></svg>",
        }
    ]

    redacted = _redact_tool_outputs_for_synth(outputs, {"tools_by_name": {}})

    assert "Stock chart generated for NVDA." in redacted[0]["output"]
    assert "structured_payload" not in redacted[0]["output"]


def test_duplicate_chart_artifacts_share_one_placeholder():
    registry = {
        "artifact_injection": {"enabled": True},
        "tools_by_name": {
            "chart": {
                "artifact": {
                    "produces_artifact": True,
                    "artifact_key": "svg",
                    "artifact_type": "svg",
                    "injection_mode": "verbatim",
                    "placeholder": "{{ARTIFACT:chart}}",
                }
            }
        },
    }
    outputs = [
        {"name": "chart", "output": '{"svg":"<svg></svg>"}'},
        {"name": "chart", "output": '{"svg":"<svg><text>new</text></svg>"}'},
    ]

    artifacts = _extract_artifacts_from_tool_outputs(outputs, registry)

    assert len(artifacts) == 1
