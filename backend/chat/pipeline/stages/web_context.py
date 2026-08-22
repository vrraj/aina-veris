"""Optional web-context stage for the chat pipeline."""

from typing import Any, Callable, Dict, List
import logging

from backend.stream_emit import emit_stage
from backend.chat.pipeline.contracts import PipelineExecutionContext

logger = logging.getLogger(__name__)


def run_web_context_stage(
    *,
    context: PipelineExecutionContext,
    use_web_search: bool,
    get_web_context: Callable[[str, List[Dict[str, Any]]], List[Dict[str, Any]]],
    effective_query: str,
    retrieval_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Fetch supplemental web context, falling back to an empty result on errors."""
    show_processing_steps = context.show_processing_steps
    req_id = context.req_id
    log_origin = context.log_origin
    if not use_web_search:
        return []

    try:
        if show_processing_steps:
            emit_stage(req_id, "Establish Web Context")
        return get_web_context(effective_query, retrieval_results or []) or []
    except Exception as exc:
        logger.debug("[WEB] (%s) ignored web context due to error: %s", log_origin, exc)
        return []
