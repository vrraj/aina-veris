"""Final answer, citation filtering, and response packing stage."""

from typing import Any, Dict, List
import logging
import re

from backend.chat.pipeline.context import collapse_sources
from backend.chat.pipeline.contracts import PipelineExecutionContext
from backend.chat.pipeline.tools import _inject_registered_artifacts

logger = logging.getLogger(__name__)
from backend.chat.pipeline.llm_io import (
    extract_reasoning_from_responses,
    extract_text_from_responses,
)


def run_final_response_stage(
    *,
    context: PipelineExecutionContext,
    inference_response: Any,
    answer_override: str | None,
    reranked: List[Dict[str, Any]],
    web_context: List[Dict[str, Any]],
    context_items: List[Dict[str, Any]],
    indexed_sources: List[Dict[str, Any]],
    sources_section: str,
    display_sources: bool,
    vectors_retrieved: int,
    tools_used: List[str],
    tool_sources: List[Dict[str, str]] | None = None,
    artifacts: List[Dict[str, str]] | None = None,
) -> Dict[str, Any]:
    """Select the answer, filter cited sources, and pack the final response."""
    metrics = context.metrics
    rewrite_display = context.rewrite_display
    query_plan_display = context.query_plan_display
    sources = (reranked or []) + (web_context or [])

    artifacts = artifacts or []
    tool_sources = tool_sources or []

    if answer_override is not None:
        answer = answer_override or ""
        try:
            if "--- External Tool Results ---" in answer or not sources:
                sources = []
                sources_section = ""
        except Exception:
            pass
    else:
        answer = extract_text_from_responses(inference_response) or ""
        raw_answer = (answer or "").rstrip()
        if "NO_SUPPORTED_SOURCES" in raw_answer:
            raw_answer = re.sub(
                r"[ \t]*NO_SUPPORTED_SOURCES[ \t]*",
                "",
                raw_answer,
            ).strip()
            raw_answer = re.sub(r"\n{3,}", "\n\n", raw_answer)
            answer = raw_answer

        lower_answer = (raw_answer or "").lower()
        if (
            "the provided context does not contain" in lower_answer
            or "the context provided does not contain" in lower_answer
            or "provided context does not" in lower_answer
            or "context does not" in lower_answer
        ):
            answer = raw_answer
            sources = []
            sources_section = ""

    try:
        if artifacts:
            answer = _inject_registered_artifacts(answer, artifacts)

        answer_for_citations = str(answer or "")
        cited_doc_indices = {
            int(value) for value in re.findall(r"\[(\d+)\]", answer_for_citations)
        }
        cited_web_indices = {
            int(value)
            for value in re.findall(
                r"\[web-(\d+)\]",
                answer_for_citations,
                flags=re.I,
            )
        }
        cited_tool_indices = {
            int(value)
            for value in re.findall(
                r"\[tool-(\d+)\]",
                answer_for_citations,
                flags=re.I,
            )
        }
        if answer_override is not None and tool_sources and not (
            cited_doc_indices or cited_web_indices or cited_tool_indices
        ):
            cited_tool_indices = set(range(1, len(tool_sources) + 1))

        if not cited_doc_indices and not cited_web_indices and not cited_tool_indices:
            sources = []
            sources_section = ""
        else:
            if cited_doc_indices:
                filtered_indexed = [
                    source
                    for source in indexed_sources
                    if int(source.get("index") or 0) in cited_doc_indices
                ]
                sources_section = (
                    "\n<sources>Sources</sources>:\n"
                    + collapse_sources(filtered_indexed)
                    if filtered_indexed
                    else ""
                )
                try:
                    sources = [
                        item
                        for index, item in enumerate(context_items, start=1)
                        if index in cited_doc_indices
                    ]
                except Exception:
                    sources = []
            else:
                sources_section = ""
                sources = []

            if cited_web_indices and web_context:
                web_notes = "\n" + "\n".join(
                    [
                        f"- [web-{index}] {web_context[index - 1].get('url', 'Web result')}"
                        for index in sorted(cited_web_indices)
                        if 1 <= index <= len(web_context)
                    ]
                )
                sources_section = (
                    sources_section or "\n<sources>Sources</sources>:\n"
                ) + web_notes
                try:
                    sources.extend(
                        [
                            web_context[index - 1]
                            for index in sorted(cited_web_indices)
                            if 1 <= index <= len(web_context)
                        ]
                    )
                except Exception:
                    pass

            if cited_tool_indices:
                selected_tool_sources = [
                    source
                    for index, source in enumerate(tool_sources, start=1)
                    if index in cited_tool_indices
                ]
                tool_notes = "\n" + "\n".join(
                    f"- [tool-{index}] {source.get('url', 'Tool result')}"
                    for index, source in enumerate(tool_sources, start=1)
                    if index in cited_tool_indices
                )
                sources_section = (
                    sources_section or "\n<sources>Sources</sources>:\n"
                ) + tool_notes
                sources.extend(selected_tool_sources)
    except Exception:
        pass

    try:
        if not display_sources:
            sources = []
            sources_section = ""
    except Exception:
        pass

    try:
        metrics.finalize_turn()
        turn_metrics, conversation_snapshot = metrics.snapshot()
    except Exception:
        turn_metrics = metrics.turn
        conversation_snapshot = {
            "tokens": {
                "embedding": 0,
                "llm_input": 0,
                "llm_output": 0,
                "conversation_total": 0,
            },
            "costs": {"conversation_total": 0.0},
        }

    final_answer = answer.rstrip("\n") + sources_section
    if artifacts:
        final_answer = _inject_registered_artifacts(final_answer, artifacts)
    final_answer = re.sub(
        r"<sources>Sources</sources>:",
        "Sources:",
        final_answer,
    )

    reasoning = None
    try:
        if inference_response is not None:
            reasoning = extract_reasoning_from_responses(inference_response)
    except Exception:
        reasoning = None

    return {
        "answer": final_answer,
        "sources": sources,
        "turn_metrics": turn_metrics,
        "conversation_totals": conversation_snapshot,
        "metrics": {"vectors_retrieved": vectors_retrieved},
        "tools_used": tools_used,
        "rewrite_display": rewrite_display,
        "query_plan_display": query_plan_display,
        "reasoning": reasoning,
    }
