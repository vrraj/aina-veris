"""Typed contracts shared by chat pipeline stages."""

from dataclasses import dataclass, field
from typing import Any, Dict, List

PipelineResponse = Dict[str, Any]
RetrievalItem = Dict[str, Any]


@dataclass(frozen=True, slots=True)
class PipelineExecutionContext:
    settings: Any
    params: Dict[str, Any]
    stage_specs: Dict[str, Any]
    req_id: str
    log_origin: str
    show_processing_steps: bool
    metrics: Any
    stage_model_keys: Dict[str, Any]
    rewrite_display: Dict[str, Any]
    query_plan_display: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TurnResolutionStageResult:
    effective_query: str
    early_response: PipelineResponse | None = None


# Compatibility alias for callers that still use the previous stage name.
RewriteStageResult = TurnResolutionStageResult


@dataclass(slots=True)
class RetrievalStageResult:
    results: List[RetrievalItem]
    skip_rerank: bool
    count: int
    kept: int
    effective_query: str | None = None
    ranking_context: Dict[str, Any] | None = None
    early_response: PipelineResponse | None = None


@dataclass(slots=True)
class RerankStageResult:
    reranked: List[RetrievalItem]
    kept: int
    early_response: PipelineResponse | None = None


@dataclass(slots=True)
class HistoryStageResult:
    recent_block_str: str
    summary_text: str


@dataclass(slots=True)
class ContextAssemblyStageResult:
    context_items: List[RetrievalItem]
    context_text: str
    indexed_sources: List[Dict[str, Any]]
    sources_section: str
    prompt_domain: str
    prompt_input: Any


@dataclass(slots=True)
class InferenceStageResult:
    response: Any
    temperature: Any
    max_output_tokens: Any
    early_response: PipelineResponse | None = None


@dataclass(slots=True)
class ToolExecutionStageResult:
    answer_override: str | None
    tools_used: List[str]
    artifacts: List[Dict[str, str]]
    tool_sources: List[Dict[str, str]] = field(default_factory=list)
    early_response: PipelineResponse | None = None
