from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RetrievalEvalRequest(BaseModel):
    query: str
    active_domain: Optional[str] = ""
    search_mode: str = "dense"
    score_threshold: Optional[float] = 0.35
    top_k: int = 8
    query_filter: Optional[Dict[str, Any]] = None
    exact: bool = False
    with_payload: bool = True

    use_colbert: bool = False
    colbert_top_n: int = 8

    enable_cross_encoder_rerank: bool = True
    cross_encoder_top_n: int = 5

    split_compound_queries: bool = False
    max_compound_queries: int = Field(default=4, ge=2, le=8)

    ensure_subquery_coverage: Optional[bool] = None
    min_results_per_subquery: Optional[int] = Field(default=None, ge=1, le=3)
    coverage_max_reserved: Optional[int] = Field(default=None, ge=1, le=20)

    # Backward-compat fields still accepted if older frontend sends them.
    colbert_score_threshold: float = 0.0
    max_items_for_cross_encoder: int = 8
    reranked_top_n: int = 5


class RetrievalEvalResponse(BaseModel):
    domain: Dict[str, Any]
    retrieval: Dict[str, Any]
    colbert: Optional[Dict[str, Any]] = None
    reranked: Dict[str, Any]
    coverage: Dict[str, Any] = Field(default_factory=dict)
    decomposition: Dict[str, Any] = Field(default_factory=dict)
    payload_echo: Dict[str, Any] = Field(default_factory=dict)


# --- Labeled dataset evaluation (run-set) ---


class EvalDatasetCase(BaseModel):
    query: str
    expected_document: str
    expected_page: Optional[int] = None
    must_contain: list[str] = Field(default_factory=list)
    must_contain_mode: str = "any"


class EvalDatasetPayload(BaseModel):
    description: Optional[str] = ""
    queries: list[EvalDatasetCase]


class RetrievalEvalRunSetRequest(BaseModel):
    dataset: str
    domains: list[str] = Field(min_length=1, max_length=6)
    search_mode: str = "hybrid"
    score_threshold: Optional[float] = 0.35
    top_k: int = Field(default=8, ge=1, le=50)
    query_filter: Optional[Dict[str, Any]] = None
    exact: bool = False

    use_colbert: bool = False
    colbert_top_n: int = 8

    enable_cross_encoder_rerank: bool = True
    cross_encoder_top_n: int = 5

    split_compound_queries: bool = False
    max_compound_queries: int = Field(default=4, ge=2, le=8)

    ensure_subquery_coverage: Optional[bool] = None
    min_results_per_subquery: Optional[int] = Field(default=None, ge=1, le=3)
    coverage_max_reserved: Optional[int] = Field(default=None, ge=1, le=20)
