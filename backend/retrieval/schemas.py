from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EmbeddingSpec:
    task: str
    runtime: str
    provider: str
    model: str
    dimensions: int | None = None
    normalize: bool = True
    batch_size: int = 32
    device: str | None = None
    extra: Dict[str, Any] = field(default_factory=dict)
    vector_type: str = "dense"  # "dense" or "sparse"
    # Vector types a unified single-pass model emits per encode call
    # (e.g. BGE-M3: ["dense", "sparse"]). Empty for single-output models.
    emits: List[str] = field(default_factory=list)


@dataclass
class EmbeddingResult:
    vectors: List[List[float]]
    model: str
    dimensions: int | None
    runtime: str
    # Sparse outputs from a unified single-pass encode, aligned with `texts`
    # as {"indices": [...], "values": [...]} dicts. None unless the model
    # emits sparse alongside dense.
    sparse_vectors: List[Dict[str, Any]] | None = None
    usage: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RerankSpec:
    task: str
    runtime: str
    provider: str
    model: str
    top_n: int | None = None
    batch_size: int = 16
    device: str | None = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RerankItem:
    index: int
    score: float
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RerankResult:
    items: List[RerankItem]
    model: str
    runtime: str
    usage: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    query: str
    rewritten_query: str | None
    candidates: List[Dict[str, Any]]
    reranked: List[Dict[str, Any]]
    context_text: str
    sources: List[Dict[str, Any]]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompoundQueryPlan:
    """Normalized query decomposition consumed by retrieval orchestration."""

    original_query: str
    normalized_query: str
    queries: List[str]
    is_compound: bool
    reason: str = ""
