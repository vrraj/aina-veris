"""Tests for retrieval eval dataset store and run-set judgment."""

import pytest
import yaml

from backend.retrieval import eval_datasets as m


@pytest.fixture()
def evals_dir(tmp_path, monkeypatch):
    directory = tmp_path / "evals"
    directory.mkdir()
    monkeypatch.setattr(m, "_evals_dir", lambda: directory)
    return directory


def _case(**overrides):
    case = {
        "query": "output swing range",
        "expected_document": "sit1534",
        "expected_page": 5,
        "must_contain": ["250 mV", "800 mV"],
        "must_contain_mode": "any",
    }
    case.update(overrides)
    return case


# --- name / path validation ---


def test_dataset_name_rejects_traversal(evals_dir):
    for bad in ["../secrets", "a/b", "..", "", "x.yaml", "na me"]:
        with pytest.raises(m.EvalDatasetError):
            m.load_dataset(bad)
        with pytest.raises(m.EvalDatasetError):
            m.save_dataset(bad, {"queries": [{"query": "q", "expected_document": "d"}]})


def test_dataset_name_allows_safe_names(evals_dir):
    for name in ["semiconductor_datasheets", "set-1", "A_B"]:
        m._dataset_path(name)  # must not raise


# --- dataset validation ---


def test_validate_dataset_requires_queries(evals_dir):
    with pytest.raises(m.EvalDatasetError):
        m.validate_dataset({"queries": []})
    with pytest.raises(m.EvalDatasetError):
        m.validate_dataset({"name": "x"})


def test_validate_case_requires_query_and_document(evals_dir):
    with pytest.raises(m.EvalDatasetError):
        m.validate_dataset({"queries": [{"expected_document": "d"}]})
    with pytest.raises(m.EvalDatasetError):
        m.validate_dataset({"queries": [{"query": "q"}]})


def test_validate_case_normalizes(evals_dir):
    data = m.validate_dataset({
        "description": "  demo ",
        "queries": [{
            "query": " q ",
            "expected_document": " doc ",
            "expected_page": "3",
            "must_contain": [" a "],
        }],
    })
    case = data["queries"][0]
    assert case["query"] == "q"
    assert case["expected_page"] == 3
    assert case["must_contain"] == ["a"]
    assert case["must_contain_mode"] == "any"


def test_save_load_roundtrip(evals_dir):
    payload = {"description": "d", "queries": [_case()]}
    m.save_dataset("demo", payload)
    loaded = m.load_dataset("demo")
    assert loaded["name"] == "demo"
    assert loaded["queries"][0]["query"] == "output swing range"


def test_save_writes_backup(evals_dir):
    m.save_dataset("demo", {"queries": [_case()]})
    m.save_dataset("demo", {"queries": [_case(query="other")]})
    backup = (evals_dir / "demo.yaml.bak").read_text()
    assert "output swing range" in yaml.safe_load(backup)["queries"][0]["query"]


def test_delete_keeps_backup(evals_dir):
    m.save_dataset("demo", {"queries": [_case()]})
    result = m.delete_dataset("demo")
    assert result["ok"] is True
    assert not (evals_dir / "demo.yaml").exists()
    assert (evals_dir / "demo.yaml.bak").exists()


# --- judgment ---


def _payload(**overrides):
    payload = {
        "source": "file://SiT1534__abc.pdf",
        "document_id": "sha256:deadbeef",
        "title": "SiT1534 Datasheet",
        "page_numbers": [5],
        "text": "the swing can be programmed between 250 mV and 800 mV",
        "display_text": "",
    }
    payload.update(overrides)
    return payload


def test_judge_item_doc_hit_and_content(evals_dir):
    verdict = m.judge_item({"payload": _payload()}, _case())
    assert verdict["doc_hit"] is True
    assert verdict["page_hit"] is True
    assert verdict["content_hit"] is True
    assert verdict["matched_strings"] == ["250 mV", "800 mV"]


def test_judge_item_content_all_mode(evals_dir):
    verdict = m.judge_item(
        {"payload": _payload()},
        _case(must_contain=["250 mV", "missing"], must_contain_mode="all"),
    )
    assert verdict["doc_hit"] is True
    assert verdict["content_hit"] is False


def test_judge_item_doc_miss_disables_page_and_content(evals_dir):
    verdict = m.judge_item(
        {"payload": _payload(source="file://other.pdf", title="other")},
        _case(),
    )
    assert verdict["doc_hit"] is False
    assert verdict["page_hit"] is None
    assert verdict["content_hit"] is None


def test_judge_item_case_and_whitespace_insensitive(evals_dir):
    payload = _payload(text="SWING   is 250   mV nominal")
    verdict = m.judge_item({"payload": payload}, _case(must_contain=["250 mv"]))
    assert verdict["content_hit"] is True


def test_judge_query_result_uses_reranked_items(evals_dir):
    result = {
        "retrieval": {"results": [{"payload": _payload(
            source="file://other.pdf", document_id="sha256:other", title="Other Doc"
        )}]},
        "reranked": {"items": [{"item": {"payload": _payload()}, "cross_encoder_score": 0.9}]},
        "decomposition": {"queries": ["q1"]},
    }
    row = m.judge_query_result(result, _case())
    assert row["doc_hit"] is True
    assert row["first_hit_rank"] == 1
    assert row["retrieval_first_hit_rank"] is None
    assert row["retrieval_doc_hit"] is False


def test_judge_query_result_rank_of_second_hit(evals_dir):
    result = {
        "retrieval": {"results": []},
        "reranked": {"items": [
            {"item": {"payload": _payload(source="file://nope.pdf", title="n")}},
            {"item": {"payload": _payload()}},
        ]},
    }
    row = m.judge_query_result(result, _case())
    assert row["first_hit_rank"] == 2
    assert row["doc_hit"] is True


# --- aggregate ---


def test_summarize_metrics(evals_dir):
    rows = [
        {"doc_hit": True, "first_hit_rank": 1, "page_hit": True, "content_hit": True,
         "retrieval_doc_hit": True, "retrieval_first_hit_rank": 2, "error": None},
        {"doc_hit": False, "first_hit_rank": None, "page_hit": None, "content_hit": None,
         "retrieval_doc_hit": True, "retrieval_first_hit_rank": 1, "error": None},
        {"doc_hit": False, "first_hit_rank": None, "page_hit": None, "content_hit": None,
         "retrieval_doc_hit": False, "retrieval_first_hit_rank": None, "error": "boom"},
    ]
    agg = m.summarize(rows)
    assert agg["queries"] == 3
    assert agg["errors"] == 1
    assert agg["doc_hits"] == 1
    assert abs(agg["hit_rate"] - 1 / 3) < 1e-9
    assert abs(agg["mrr"] - 1 / 3) < 1e-9  # only one hit at rank 1
    assert abs(agg["retrieval_hit_rate"] - 2 / 3) < 1e-9
    # retrieval hits at ranks 2 and 1 -> (1/2 + 1) / 3
    assert abs(agg["retrieval_mrr"] - (0.5 + 1.0) / 3) < 1e-9
    assert agg["page_hit_rate"] == 1.0
    assert agg["content_hit_rate"] == 1.0
