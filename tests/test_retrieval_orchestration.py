import os

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.retrieval.orchestration import run_retrieval_orchestration


class FakeService:
    def __init__(self):
        self.calls = []

    def run_pipeline(self, **kwargs):
        self.calls.append(kwargs)
        return {"retrieval": {"results": []}, "reranked": {"items": []}}


def test_shared_orchestration_normalizes_and_decomposes():
    service = FakeService()
    result = run_retrieval_orchestration(
        query="mount whitny elevtion and permts",
        active_domain="mountains",
        split_compound_queries=True,
        max_compound_queries=3,
        search_mode="dense",
        top_k=3,
        score_threshold=0.35,
        query_filter=None,
        with_payload=True,
        exact=False,
        use_colbert=False,
        colbert_top_n=3,
        enable_cross_encoder_rerank=False,
        cross_encoder_top_n=2,
        service=service,
        decomposition_generator=lambda _prompt: {
            "normalized_query": "Mount Whitney elevation and permits",
            "is_compound": True,
            "queries": ["Mount Whitney elevation", "Mount Whitney permits"],
        },
    )

    assert service.calls[0]["query"] == "Mount Whitney elevation and permits"
    assert service.calls[0]["queries"] == ["Mount Whitney elevation", "Mount Whitney permits"]
    assert result["decomposition"]["is_compound"] is True
