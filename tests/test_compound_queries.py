import pytest

from backend.retrieval.compound_queries import (
    decompose_compound_query,
)


def test_decompose_compound_query_normalizes_and_limits_queries():
    result = decompose_compound_query(
        "  Compare   Mount Whitney permits and weather  ",
        max_queries=2,
        prompt="decompose",
        generator=lambda _prompt: {
            "normalized_query": "Compare Mount Whitney permits and weather",
            "is_compound": True,
            "queries": [
                " Mount Whitney permit requirements ",
                "Mount Whitney weather",
                "Mount Whitney trail conditions",
            ],
            "reason": "separate permit and weather needs",
        },
    )

    assert result.original_query == "Compare Mount Whitney permits and weather"
    assert result.normalized_query == "Compare Mount Whitney permits and weather"
    assert result.queries == [
        "Mount Whitney permit requirements",
        "Mount Whitney weather",
    ]
    assert result.is_compound is True
    assert result.reason == "separate permit and weather needs"


def test_decompose_compound_query_accepts_fenced_json_text():
    result = decompose_compound_query(
        "Compare elevation and permits",
        prompt="decompose",
        generator=lambda _prompt: """
        ```json
        {"normalized_query": "Compare mountain elevation and permits", "is_compound": true, "queries": ["mountain elevation", "mountain permits"]}
        ```
        """,
    )

    assert result.is_compound is True
    assert result.normalized_query == "Compare mountain elevation and permits"
    assert result.queries == ["mountain elevation", "mountain permits"]


def test_decompose_compound_query_uses_normalized_query_when_not_compound():
    result = decompose_compound_query(
        "mount whitny elevtion",
        prompt="decompose",
        generator=lambda _prompt: {
            "normalized_query": "Mount Whitney elevation",
            "is_compound": False,
            "queries": [],
            "reason": "single information need",
        },
    )

    assert result.original_query == "mount whitny elevtion"
    assert result.normalized_query == "Mount Whitney elevation"
    assert result.queries == ["Mount Whitney elevation"]
    assert result.is_compound is False


@pytest.mark.parametrize(
    ("generated", "expected_reason"),
    [
        ({"is_compound": False, "queries": []}, "not_compound"),
        ({"is_compound": True, "queries": ["only one query"]}, "invalid_compound_queries"),
        ({"is_compound": True, "queries": ["same", " SAME "]}, "invalid_compound_queries"),
        ("not json", "decomposition_error"),
    ],
)
def test_decompose_compound_query_falls_back_to_original(generated, expected_reason):
    result = decompose_compound_query(
        "original query",
        prompt="decompose",
        generator=lambda _prompt: generated,
    )

    assert result.queries == ["original query"]
    assert result.normalized_query == "original query"
    assert result.is_compound is False
    assert result.reason == expected_reason


def test_decompose_compound_query_falls_back_when_generator_fails():
    def fail(_prompt):
        raise RuntimeError("provider unavailable")

    result = decompose_compound_query("original query", generator=fail, prompt="decompose")

    assert result.queries == ["original query"]
    assert result.normalized_query == "original query"
    assert result.reason == "decomposition_error"


def test_decompose_compound_query_requires_query():
    with pytest.raises(ValueError, match="query is required"):
        decompose_compound_query("  ", generator=lambda _prompt: {}, prompt="decompose")
