"""Tool execution and synthesis stage for the chat pipeline."""

from typing import Any, Callable, Dict, List
import json
import logging
import re

from backend.chat.pipeline.context import build_tools_synth_messages
from backend.chat.pipeline.contracts import PipelineExecutionContext, ToolExecutionStageResult
from backend.chat.pipeline.errors import build_rate_limit_response
from backend.chat.pipeline.llm_io import (
    consume_text_stream,
    extract_text_from_responses,
    extract_usage_from_responses,
    responses_create,
    responses_stream,
)
from backend.chat.pipeline.tools import (
    extract_tool_calls,
    parse_tool_args,
    _extract_artifacts_from_tool_outputs,
    _inject_registered_artifacts,
    _load_tool_registry,
    _redact_tool_outputs_for_synth,
    _strip_svg_from_messages,
)
from backend.integrations.mcp.client import get_mcp_runtime_for_tool
from backend.integrations.mcp.adapters import NormalizedToolResult
from backend.chat.prompt_registry import resolve_tools_synth_prompt
from backend.llm.llm_client import LLMError
from backend.stream_emit import emit_stage
# Reuse the local stock tool's point normalizer so MCP responses can be parsed
# identically when we synthesize SVGs on the fly.
from backend.tools.get_stock_price_history import _extract_points as _extract_stock_points
from backend.tools.get_timeseries_sparklines_svg import generate_timeseries_sparklines

logger = logging.getLogger(__name__)


def _normalize_synthesized_answer(text: str) -> str:
    """Keep supported partial answers while removing the insufficiency marker.

    Tool synthesis may answer document-backed facets successfully while one
    tool-backed facet remains unsupported. The marker applies to that facet;
    it must not discard the rest of the synthesized response.
    """
    normalized = re.sub(
        r"[ \t]*NO_SUPPORTED_SOURCES[ \t]*",
        "",
        str(text or ""),
    ).strip()
    return re.sub(r"\n{3,}", "\n\n", normalized)


def _is_insufficient_synthesis(answer: str) -> bool:
    """Identify a synthesis refusal that must not hide usable tool output."""
    normalized = str(answer or "").lower()
    return (
        "i couldn't find any information to answer" in normalized
        or "i could not find any information to answer" in normalized
        or "cannot answer from the context" in normalized
    )


def _format_tool_fallback(tool_answer_text: str, tools_text: str) -> str:
    answer = (tool_answer_text or "").strip()
    outputs = (tools_text or "").strip()
    if answer and outputs and answer not in outputs:
        return answer + "\n\n" + "--- External Tool Results ---\n" + outputs
    return "--- External Tool Results ---\n" + outputs


def _extract_svg_from_tool_outputs(tool_outputs: List[Dict[str, Any]]) -> str:
    for tool_output in tool_outputs or []:
        output = str(tool_output.get("output") or "")
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                svg = parsed.get("svg")
                if isinstance(svg, str) and svg.strip():
                    return svg.strip()
        except Exception:
            pass

        try:
            match = re.search(r"<svg\\b[\\s\\S]*?</svg>", output, flags=re.IGNORECASE)
            if match and match.group(0).strip():
                return match.group(0).strip()
        except Exception:
            pass
    return ""


def _maybe_synthesize_stock_svg(args: Dict[str, Any] | None, result_text: Any) -> tuple[Any, bool]:
    if not isinstance(result_text, dict):
        return result_text, False
    svg_val = result_text.get("svg")
    if isinstance(svg_val, str) and "<svg" in svg_val.lower():
        return result_text, False

    symbol = None
    if isinstance(args, dict):
        symbol = args.get("symbol")
    if not symbol:
        symbol = result_text.get("symbol")
    if not symbol:
        symbols = result_text.get("symbols")
        if isinstance(symbols, list) and symbols:
            symbol = symbols[0]
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return result_text, False

    try:
        points = _extract_stock_points(result_text, symbol)
    except Exception as exc:
        points = []

    if not points:
        return result_text, False

    period = None
    if isinstance(args, dict):
        period = args.get("period")
    period = (result_text.get("period") or period or "1Y").strip()
    title = None
    if isinstance(args, dict):
        title = args.get("title")
    title = (result_text.get("title") or title or f"{symbol} {period}").strip()
    chart_type = args.get("chart_type") if isinstance(args, dict) else None

    rendered = generate_timeseries_sparklines(
        data=points,
        period=period,
        title=title,
        width=760,
        height=320,
        margin={"top": 16, "right": 20, "bottom": 44, "left": 58},
        up_color="#16a34a",
        down_color="#dc2626",
        grid_color="rgba(148,163,184,0.35)",
        axis_color="#94a3b8",
        label_color="#64748b",
        chart_type=chart_type,
    )

    svg_payload = (rendered or {}).get("svg", "")
    if not isinstance(svg_payload, str) or "<svg" not in svg_payload.lower():
        return result_text, False

    enriched = dict(result_text)
    enriched["svg"] = svg_payload
    enriched.setdefault("data", points)
    enriched["symbol"] = symbol
    enriched["period"] = (rendered or {}).get("period") or period
    if rendered.get("summary"):
        enriched["summary"] = rendered["summary"]
    enriched["data_points"] = (rendered or {}).get("data_points", len(points))
    return enriched, True


def run_tool_execution_stage(
    *,
    context: PipelineExecutionContext,
    enable_tools: bool,
    prompt_input: Any,
    inference_response: Any,
    history: List[Dict[str, Any]],
    message: str,
    reranked: List[Dict[str, Any]],
    prompt_domain: str,
    summary_text: str,
    recent_block_str: str,
    context_text: str,
    temperature: Any,
    max_output_tokens: Any,
    get_executor: Callable[[str], Any],
    max_tool_calls: int | None = None,
) -> ToolExecutionStageResult:
    """Execute first-pass tool calls and synthesize their final answer."""
    settings_obj = context.settings
    stage_specs = context.stage_specs
    show_processing_steps = context.show_processing_steps
    req_id = context.req_id
    log_origin = context.log_origin
    metrics = context.metrics
    stage_model_keys = context.stage_model_keys
    rewrite_display = context.rewrite_display
    answer_override: str | None = None
    tool_answer_text = ""
    successful_tool_answer_text = ""
    used_tools: List[str] = []
    artifacts: List[Dict[str, str]] = []
    tool_sources: List[Dict[str, str]] = []

    if not (enable_tools and isinstance(prompt_input, list)):
        return ToolExecutionStageResult(
            answer_override=answer_override,
            tools_used=[],
            artifacts=artifacts,
        )

    try:
        tool_registry = _load_tool_registry(settings_obj)
        tools_by_name = (
            tool_registry.get("tools_by_name")
            if isinstance(tool_registry, dict)
            else {}
        )
        if not isinstance(tools_by_name, dict):
            tools_by_name = {}
        
        tool_calls = extract_tool_calls(inference_response)
        try:
            logger.debug(
                "[TOOLS] (%s) extracted_tool_calls count=%d names=%s",
                log_origin,
                len(tool_calls or []),
                [str((call or {}).get("name") or "") for call in (tool_calls or [])],
            )
        except Exception:
            pass

        if not tool_calls:
            logger.debug("[TOOLS] (%s) no_tool_calls_from_inference", log_origin)
            raise StopIteration

        if max_tool_calls is not None and max_tool_calls <= 0:
            logger.info("[TOOLS] (%s) tool execution disabled by request limit", log_origin)
            raise StopIteration
        if max_tool_calls is not None:
            tool_calls = tool_calls[:max_tool_calls]

        if show_processing_steps:
            emit_stage(req_id, "Tool Calls")

        tool_outputs: List[Dict[str, Any]] = []
        chat_context = list(history or []) + [{"role": "user", "content": message}]
        tools_with_doc_context = set(
            getattr(settings_obj, "tools_with_document_context", []) or []
        )

        for call in tool_calls:
            name = call.get("name") or ""
            call_id = call.get("id") or call.get("tool_call_id")
            args = parse_tool_args(call.get("args"))

            mcp_runtime = get_mcp_runtime_for_tool(name)
            call_origin = "mcp" if mcp_runtime else "local"
            display_name = f"{call_origin}:{name}" if name else call_origin
            if show_processing_steps:
                emit_stage(req_id, f"Calling Tool: {display_name}")
            executor = (
                get_executor(name, mcp_runtime=mcp_runtime)
                if mcp_runtime
                else get_executor(name)
            )
            logger.info(
                "[TOOLS] (%s) tool_dispatch origin=%s name=%s call_id=%s executor_found=%s mcp_server=%s",
                log_origin,
                call_origin,
                name,
                call_id,
                bool(executor),
                mcp_runtime.get("mcp_server") if mcp_runtime else None,
            )

            tool_succeeded = False
            normalized_sources: List[Dict[str, str]] = []
            if not executor:
                result_text: Any = f"Tool '{name}' is not available."
                logger.warning("[TOOLS] Tool not found: %s", name)
            else:
                try:
                    existing_context = None
                    if name in tools_with_doc_context:
                        existing_context = [
                            {
                                "url": (item.get("payload") or {}).get("url")
                                or (item.get("payload") or {}).get("url_lower", ""),
                                "title": (item.get("payload") or {}).get("title")
                                or "",
                                "snippet": (item.get("payload") or {}).get("text")
                                or (item.get("payload") or {}).get("snippet")
                                or "",
                            }
                            for item in (reranked or [])
                        ]

                    registry_entry = (
                        tools_by_name.get(name)
                        if isinstance(tools_by_name, dict)
                        else None
                    )
                    runtime = (
                        registry_entry.get("runtime")
                        if isinstance(registry_entry, dict)
                        and isinstance(registry_entry.get("runtime"), dict)
                        else {}
                    )
                    endpoint = (
                        runtime.get("endpoint")
                        if isinstance(runtime.get("endpoint"), dict)
                        else {}
                    )
                    logger.info(
                        "[TOOLS] (%s) tool_execute_start origin=%s name=%s args=%s endpoint_type=%s endpoint_url=%s",
                        log_origin,
                        call_origin,
                        name,
                        args,
                        str(endpoint.get("type") or ""),
                        str(endpoint.get("url") or ""),
                    )
                    executor_kwargs = {
                        "existing_context": existing_context,
                        "tool_registry_entry": registry_entry,
                    }
                    if call_origin != "mcp":
                        executor_kwargs["tool_runtime"] = runtime

                    result_text = executor(
                        args,
                        chat_context,
                        **executor_kwargs,
                    )
                    tool_succeeded = True
                    if isinstance(result_text, NormalizedToolResult):
                        normalized_sources = [source.as_dict() for source in result_text.sources]
                        result_text = result_text.data
                    logger.info(
                        "[TOOLS] (%s) tool_execute_raw_output origin=%s name=%s args=%s output_preview=%s",
                        log_origin,
                        call_origin,
                        name,
                        args,
                        str(result_text)[:256],
                    )
                    synthesized = False
                    if name == "get_stock_price_history":
                        result_text, synthesized = _maybe_synthesize_stock_svg(args, result_text)
                        if synthesized:
                            try:
                                data_points = len(result_text.get("data") or []) if isinstance(result_text, dict) else 0
                            except Exception:
                                data_points = 0
                            pass
                        else:
                            pass
                    if (
                        name
                        and isinstance(result_text, dict)
                        and isinstance(result_text.get("svg"), str)
                        and "<svg" in result_text.get("svg", "").lower()
                    ):
                        registry_entry = tools_by_name.setdefault(name, {})
                        artifact_cfg = registry_entry.setdefault("artifact", {})
                        if not artifact_cfg.get("produces_artifact"):
                            artifact_cfg["produces_artifact"] = True
                        artifact_cfg.setdefault("artifact_type", "svg")
                        artifact_cfg.setdefault("artifact_key", "svg")
                        artifact_cfg.setdefault("injection_mode", "verbatim")
                        artifact_cfg.setdefault(
                            "placeholder",
                            f"{{{{ARTIFACT:{name}_svg}}}}",
                        )
                        tools_by_name[name] = registry_entry
                        pass
                    logger.debug(
                        "[TOOLS] (%s) tool_execute_done name=%s output_type=%s output_len=%d",
                        log_origin,
                        name,
                        type(result_text).__name__,
                        len(str(result_text or "")),
                    )
                except Exception as exc:
                    logger.error(
                        "[TOOLS] (%s) tool_execute_error origin=%s name=%s err=%s",
                        log_origin,
                        call_origin,
                        name,
                        exc,
                        exc_info=True,
                    )
                    result_text = f"Tool '{name}' failed: {exc}"

            try:
                if result_text is None:
                    result_text = ""
                if isinstance(result_text, str) and not result_text.strip():
                    result_text = f"Tool '{name}' executed but returned no results."
            except Exception:
                pass

            if name:
                used_tools.append(f"{call_origin}:{name}")

            # Extract SVG artifact and placeholder metadata from MCP responses before normalization
            svg_artifact = None
            artifact_placeholder = None
            if isinstance(result_text, dict):
                artifact_placeholder = result_text.get("artifact_placeholder")
                if "_svg_artifact" in result_text:
                    svg_artifact = result_text.pop("_svg_artifact")

            try:
                if isinstance(result_text, (dict, list)):
                    normalized_output = json.dumps(result_text, ensure_ascii=False)
                else:
                    normalized_output = str(result_text)
            except Exception:
                normalized_output = str(result_text)

            tool_output_entry = {
                "tool_call_id": call_id or "",
                "name": name,
                "output": normalized_output,
            }
            if normalized_sources:
                for source in normalized_sources:
                    source["citation"] = f"tool-{len(tool_sources) + 1}"
                    source["tool"] = name
                    tool_sources.append(source)
                tool_output_entry["sources"] = normalized_sources
            if artifact_placeholder:
                tool_output_entry["artifact_placeholder"] = artifact_placeholder
            if svg_artifact:
                tool_output_entry["_svg_artifact"] = svg_artifact
            tool_outputs.append(tool_output_entry)
            try:
                text = (
                    result_text.strip()
                    if isinstance(result_text, str)
                    else str(result_text).strip()
                )
                if text and not tool_answer_text:
                    tool_answer_text = text
                if text and tool_succeeded and not successful_tool_answer_text:
                    successful_tool_answer_text = text
            except Exception:
                pass

        if not tool_outputs:
            logger.debug("[TOOLS] (%s) no_tool_outputs_after_execution", log_origin)
            raise StopIteration

        artifacts = _extract_artifacts_from_tool_outputs(tool_outputs, tool_registry)
        synth_outputs = _redact_tool_outputs_for_synth(tool_outputs, tool_registry)
        tools_text = "\n\n".join(
            [
                f"[SOURCE: TOOL - {item.get('name') or 'unknown'}]\n{str(item.get('output', ''))}"
                for item in synth_outputs
            ]
        ).strip()
        if not tools_text:
            tools_text = "Tool(s) executed but returned no results."

        registry_path = str(
            getattr(settings_obj, "inference_prompt_registry_path", "") or ""
        ).strip()
        prompt_spec = resolve_tools_synth_prompt(
            registry_path=registry_path,
            domain=(prompt_domain or "").strip(),
        )
        synth_messages = build_tools_synth_messages(
            system_prompt=(prompt_spec.system_instruction or "").strip(),
            summary_text=summary_text,
            recent_block_str=recent_block_str,
            context_text=context_text,
            tool_outputs_list=synth_outputs,
            used_tools=used_tools,
            tools_text=tools_text,
            message=message,
        )
        synth_messages, stripped_svg_blocks = _strip_svg_from_messages(synth_messages)

        synth_spec = (stage_specs or {}).get("tools_synth") or {}
        synth_provider = str(synth_spec.get("provider") or "openai")
        synth_model = str(synth_spec.get("model"))
        synth_kwargs: Dict[str, Any] = dict(synth_spec.get("kwargs") or {})
        synth_kwargs["input"] = synth_messages
        if "max_output_tokens" not in synth_kwargs and max_output_tokens is not None:
            synth_kwargs["max_output_tokens"] = int(max_output_tokens)
        if "temperature" not in synth_kwargs:
            synth_kwargs["temperature"] = float(temperature)

        try:
            if show_processing_steps:
                emit_stage(req_id, "Generating Responses with Tools")
            logger.debug(
                "[TOOLS] synthesis stage provider=%s model=%s",
                synth_provider,
                synth_model,
            )

            stream_answer = bool(
                context.params.get(
                    "stream_answer",
                    getattr(settings_obj, "stream_answer", False),
                )
            )
            logger.info(
                "[STREAM] (%s) pass=tool_synthesis requested=%s tools_used=%s path=%s",
                log_origin,
                stream_answer,
                sorted({tool for tool in used_tools if tool}),
                "stream" if stream_answer else "non_stream",
            )
            if stream_answer:
                synth_response = consume_text_stream(
                    responses_stream(
                        provider=synth_provider,
                        model=synth_model,
                        **synth_kwargs,
                    ),
                    on_delta=lambda delta: emit_stage(
                        req_id,
                        "Answer Delta",
                        delta=delta,
                    ),
                )
            else:
                synth_response = responses_create(
                    provider=synth_provider,
                    model=synth_model,
                    **synth_kwargs,
                )
            combined = _normalize_synthesized_answer(
                extract_text_from_responses(synth_response)
            )
            if _is_insufficient_synthesis(combined) and successful_tool_answer_text:
                logger.warning(
                    "[TOOLS] (%s) tool synthesis reported insufficient sources despite usable tool output; using fallback",
                    log_origin,
                )
                combined = _format_tool_fallback(
                    successful_tool_answer_text,
                    tools_text,
                )
            combined = _inject_registered_artifacts(combined, artifacts)

            svg_from_tool = _extract_svg_from_tool_outputs(tool_outputs)
            has_artifact_token = bool(
                re.search(
                    r"\{+\s*ARTIFACT:[A-Za-z0-9_.:-]{1,64}\s*\}+",
                    combined or "",
                )
            )
            if svg_from_tool and has_artifact_token:
                try:
                    combined, replacements = re.subn(
                        r"\{+\s*ARTIFACT:[A-Za-z0-9_.:-]{1,64}\s*\}+",
                        svg_from_tool,
                        combined,
                    )
                except Exception:
                    pass

            chart_requested = bool(
                re.search(
                    r"\b(chart|sparkline|trend|line\s*chart|bar\s*chart|time[-\s]?series|visual)\b",
                    str(message or ""),
                    flags=re.IGNORECASE,
                )
            )
            if svg_from_tool and chart_requested:
                has_svg = "<svg" in combined.lower()
                has_svg_close = "</svg>" in combined.lower()
                if not has_svg or not has_svg_close:
                    if has_svg:
                        try:
                            prefix = combined.split("<svg", 1)[0].strip()
                        except Exception:
                            prefix = ""
                    else:
                        prefix = combined.strip()
                    combined = (
                        f"{prefix}\n\n{svg_from_tool}" if prefix else svg_from_tool
                    )

            logger.debug(
                "[TOOLS] %s tools synthesis combined before override : %s",
                log_origin,
                combined,
            )
            if combined:
                answer_override = combined
            else:
                answer_override = _format_tool_fallback(
                    tool_answer_text,
                    tools_text,
                )
            try:
                answer_override = re.sub(
                    r"\s*\[Tool results\]\s*",
                    "",
                    answer_override or "",
                ).strip()
            except Exception:
                pass

            logger.debug(
                "[TOOLS] %s tools synthesis combined: %s and answer_override %s",
                log_origin,
                combined,
                answer_override,
            )
            usage = extract_usage_from_responses(
                synth_response,
                provider=synth_provider,
            )
            if usage:
                metrics.record_stage(
                    "inference_tools_synth",
                    model=synth_model,
                    usage=usage,
                    model_key=(stage_model_keys or {}).get("tools_synth"),
                )
        except LLMError as exc:
            if (getattr(exc, "kind", "") or "") == "rate_limit":
                early_response = build_rate_limit_response(
                    req_id=req_id,
                    metrics=metrics,
                    rewrite_display=rewrite_display,
                    stage_label="tools synthesis",
                    provider=str(getattr(exc, "provider", "") or "").strip()
                    or "the tools synthesis provider",
                    model=str(getattr(exc, "model", "") or "").strip()
                    or "(unspecified model)",
                    action="combine the tool results safely",
                    tools_used=sorted({tool for tool in used_tools if tool}),
                )
                return ToolExecutionStageResult(
                    answer_override=answer_override,
                    tools_used=sorted({tool for tool in used_tools if tool}),
                    artifacts=artifacts,
                    early_response=early_response,
                )
            logger.debug(
                "[TOOLS] (%s) tools synthesis failed: %s",
                log_origin,
                exc,
                exc_info=True,
            )
            answer_override = _format_tool_fallback(tool_answer_text, tools_text)
        except Exception as exc:
            logger.debug(
                "[TOOLS] (%s) tools synthesis failed: %s",
                log_origin,
                exc,
                exc_info=True,
            )
            answer_override = _format_tool_fallback(tool_answer_text, tools_text)
    except StopIteration:
        pass
    except Exception as exc:
        logger.debug(
            "[TOOLS] (%s) tool loop failed: %s",
            log_origin,
            exc,
            exc_info=True,
        )

    if not answer_override and tool_answer_text:
        logger.debug(
            "[TOOLS] %s Falling back to tool answer text %s ",
            log_origin,
            tool_answer_text[:100],
        )
        answer_override = tool_answer_text

    return ToolExecutionStageResult(
        answer_override=answer_override,
        tools_used=sorted({tool for tool in used_tools if tool}),
        artifacts=artifacts,
        tool_sources=tool_sources,
    )
