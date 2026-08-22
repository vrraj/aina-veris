"""Inference context and prompt assembly stage."""

from typing import Any, Callable, Dict, List

from backend.chat.pipeline.context import (
    collapse_sources,
    format_context_lines,
    format_query_plan,
    format_web_context_as_text,
)
from backend.chat.pipeline.contracts import (
    ContextAssemblyStageResult,
    PipelineExecutionContext,
)
from backend.chat.prompt_registry import resolve_inference_prompt, render_full_payload
from backend.stream_emit import emit_stage


def run_context_assembly_stage(
    *,
    context: PipelineExecutionContext,
    reranked: List[Dict[str, Any]],
    kept: int,
    web_context: List[Dict[str, Any]],
    recent_block_str: str,
    summary_text: str,
    message: str,
    style: str,
    enable_tools: bool,
    debug_log: Callable[[str, str], None] | None = None,
) -> ContextAssemblyStageResult:
    """Build document context, source tracking, and the first inference input."""
    settings_obj = context.settings
    params = context.params
    show_processing_steps = context.show_processing_steps
    req_id = context.req_id
    log_origin = context.log_origin
    inference_rows = int(getattr(settings_obj, "inference_context_rows", kept) or kept)
    inference_rows = min(max(1, inference_rows), kept)
    if debug_log:
        debug_log(
            f"[CONTEXT] {log_origin}",
            f"using {inference_rows} of {kept} retrieved items",
        )

    context_items = (reranked or [])[:inference_rows]
    context_text = format_context_lines(context_items)
    indexed_sources = [
        {
            "index": index + 1,
            "url": (
                (item.get("payload") or {}).get(
                    "url_lower",
                    (item.get("payload") or {}).get("url", "unknown"),
                )
            ),
            "section": (item.get("payload") or {}).get("section", "N/A"),
            "subsection": (item.get("payload") or {}).get("subsection", "N/A"),
        }
        for index, item in enumerate(context_items)
    ]

    sources_section = "\n<sources>Sources</sources>:\n" + collapse_sources(indexed_sources)
    if web_context:
        web_notes = "\n" + "\n".join(
            [
                f"[web-{index + 1}] {item.get('url', 'Web result')}"
                for index, item in enumerate(web_context)
            ]
        )
        sources_section += web_notes

    if show_processing_steps:
        emit_stage(req_id, "Inference Context Assembly")

    try:
        prompt_domain = str((params or {}).get("prompt_domain") or "").strip()
    except Exception:
        prompt_domain = ""
    if not prompt_domain:
        try:
            prompt_domain = str(
                getattr(settings_obj, "prompt_domain_default", "") or ""
            ).strip()
        except Exception:
            prompt_domain = ""

    registry_path = str(
        getattr(settings_obj, "inference_prompt_registry_path", "") or ""
    ).strip()
    prompt_spec = resolve_inference_prompt(
        registry_path=registry_path,
        domain=prompt_domain,
    )

    web_text = format_web_context_as_text(web_context) if web_context else ""
    payload = render_full_payload(
        prompt_spec.full_payload_template,
        variables={
            "recent_block_str": (recent_block_str or "").strip(),
            "summary_text": summary_text or "",
            "context_text": context_text or "",
            "web_context": web_text or "",
            "message": message or "",
        },
    )

    if style == "messages":
        prompt_input: Any = [
            {"role": "system", "content": prompt_spec.system_instruction},
        ]
        query_plan_text = format_query_plan(context.query_plan_display)
        if query_plan_text:
            prompt_input.append(
                {"role": "user", "content": "QUERY PLAN:\n" + query_plan_text}
            )
        prompt_input.append({"role": "user", "content": payload})
    else:
        query_plan_text = format_query_plan(context.query_plan_display)
        query_plan_block = (
            "\n\nQUERY PLAN:\n" + query_plan_text if query_plan_text else ""
        )
        prompt_text = prompt_spec.system_instruction + query_plan_block + "\n\n" + payload
        prompt_input = (
            [{"role": "user", "content": prompt_text}]
            if enable_tools
            else prompt_text
        )

    return ContextAssemblyStageResult(
        context_items=context_items,
        context_text=context_text,
        indexed_sources=indexed_sources,
        sources_section=sources_section,
        prompt_domain=prompt_domain,
        prompt_input=prompt_input,
    )
