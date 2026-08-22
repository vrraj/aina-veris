from backend.retrieval.coverage import select_with_subquery_coverage


def item(point_id, matched_queries):
    return {
        "id": point_id,
        "payload": {"text": point_id},
        "compound_retrieval": {"matched_queries": matched_queries},
    }


def row(value, score):
    return {
        "original_index": 0,
        "cross_encoder_score": score,
        "item": value,
    }


def test_coverage_reserves_candidate_missing_from_reranker_top_results():
    shared = item("shared", ["permits"])
    weather = item("weather", ["weather"])
    globally_best = item("best", ["permits"])

    result = select_with_subquery_coverage(
        queries=["permits", "weather"],
        candidates=[shared, weather, globally_best],
        ranked_rows=[row(globally_best, 0.9), row(shared, 0.8)],
        final_top_n=2,
        enabled=True,
        min_results_per_subquery=1,
        max_reserved=2,
    )

    assert [entry["item"]["id"] for entry in result["items"]] == ["weather", "best"]
    assert result["coverage"]["reserved_items"] == 2
    assert result["coverage"]["guarantee_satisfied"] is True


def test_coverage_reports_subquery_without_qualified_candidate():
    permits = item("permits", ["permits"])

    result = select_with_subquery_coverage(
        queries=["permits", "weather"],
        candidates=[permits],
        ranked_rows=[row(permits, 0.9)],
        final_top_n=2,
        enabled=True,
        min_results_per_subquery=1,
        max_reserved=2,
    )

    assert result["coverage"]["uncovered_queries"] == ["weather"]
    assert result["coverage"]["guarantee_satisfied"] is False


def test_coverage_prioritizes_scarce_query_when_reservation_is_limited():
    permits = item("permits", ["permits"])
    shared = item("shared", ["permits"])
    weather = item("weather", ["weather"])

    result = select_with_subquery_coverage(
        queries=["permits", "weather"],
        candidates=[permits, shared, weather],
        ranked_rows=[row(permits, 0.9), row(shared, 0.8)],
        final_top_n=1,
        enabled=True,
        min_results_per_subquery=1,
        max_reserved=1,
    )

    assert result["items"][0]["item"]["id"] == "weather"
    assert result["coverage"]["uncovered_queries"] == ["permits"]
