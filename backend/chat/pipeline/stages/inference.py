"""First-pass inference stage for the chat pipeline."""

from typing import Any, Callable, Dict, List
import logging

from backend.chat.pipeline.errors import build_rate_limit_response, build_timeout_response
from backend.chat.pipeline.contracts import InferenceStageResult, PipelineExecutionContext
from backend.chat.pipeline.llm_io import (
    consume_text_stream,
    extract_usage_from_responses,
    responses_create,
    responses_stream,
)
from backend.llm.llm_client import LLMError, get_model_info
from backend.stream_emit import emit_stage

logger = logging.getLogger(__name__)


def _pick(params: Dict[str, Any] | None, keys: List[str], default: Any = None) -> Any:
    values = params or {}
    for key in keys:
        if key in values and values[key] is not None:
            return values[key]
    return default


def _is_web_search_requested(message: str) -> bool:
    text = (message or "").lower()
    keys = [
        "use web search",
        "search the web",
        "web search",
        "search online",
        "browse the web",
        "do a web search",
        "google this",
        "bing this",
    ]
    return any(key in text for key in keys)


def run_inference_stage(
    *,
    context: PipelineExecutionContext,
    prompt_input: Any,
    message: str,
    enable_tools: bool,
    list_tools: Callable[[], List[Dict[str, Any]]],
) -> InferenceStageResult:
    """Run the first inference pass and record its usage."""
    settings_obj = context.settings
    params = context.params
    stage_specs = context.stage_specs
    show_processing_steps = context.show_processing_steps
    req_id = context.req_id
    log_origin = context.log_origin
    metrics = context.metrics
    stage_model_keys = context.stage_model_keys
    rewrite_display = context.rewrite_display
    temperature = _pick(
        params,
        ["temperature", "inference_temperature", "INFERENCE_TEMPERATURE"],
        getattr(settings_obj, "inference_temperature", 0.7),
    )
    max_output_tokens = _pick(
        params,
        [
            "max_output_tokens",
            "max_inference_output_tokens",
            "MAX_INFERENCE_OUTPUT_TOKENS",
        ],
        getattr(settings_obj, "max_inference_output_tokens", 300),
    )
    top_p = _pick(
        params,
        ["top_p", "inference_top_p", "INFERENCE_TOP_P"],
        getattr(settings_obj, "inference_top_p", None),
    )

    logger.info("[PIPELINE] emit stage: Generating Response")
    if show_processing_steps:
        emit_stage(req_id, "Generating Response")

    inference_spec = (stage_specs or {}).get("inference") or {}
    provider = str(inference_spec.get("provider") or "openai")
    model = str(
        inference_spec.get("model")
        or getattr(settings_obj, "inference_model", "gpt-4o")
    )

    if provider == "openai" and (
        model.startswith("models/gemini") or model.startswith("gemini")
    ):
        provider = "gemini"
        logger.debug(
            "[INFERENCE AUTO-DETECT] Corrected provider to 'gemini' for model '%s'",
            model,
        )

    logger.debug("[INFERENCE DEBUG] provider=%s model=%s", provider, model)
    if model.startswith("models/gemini") and provider == "openai":
        logger.warning(
            "[INFERENCE MISMATCH] Gemini model '%s' with OpenAI provider '%s' - this will fail!",
            model,
            provider,
        )

    inference_kwargs: Dict[str, Any] = dict(inference_spec.get("kwargs") or {})
    inference_kwargs["input"] = prompt_input
    inference_kwargs["temperature"] = float(temperature)
    inference_kwargs["max_output_tokens"] = int(max_output_tokens)
    if top_p is not None:
        inference_kwargs["top_p"] = float(top_p)

    if enable_tools and isinstance(prompt_input, list):
        try:
            logger.debug("[PIPELINE] Before Tools list function")
            tools = list_tools()
            logger.debug("[PIPELINE] After Tools list function %s ", tools[:100])
            if not _is_web_search_requested(message):
                tools = [
                    tool
                    for tool in tools
                    if (
                        tool.get("name")
                        or tool.get("function", {}).get("name")
                    )
                    != "web_search"
                ]
            inference_kwargs["tools"] = tools
            try:
                offered_tool_names = [
                    str(
                        tool.get("name")
                        or tool.get("function", {}).get("name")
                        or ""
                    )
                    for tool in (tools or [])
                ]
                logger.info(
                    "[TOOLS] (%s) tools_offered_to_inference count=%d names=%s",
                    log_origin,
                    len([name for name in offered_tool_names if name]),
                    [name for name in offered_tool_names if name],
                )
            except Exception:
                pass
        except Exception:
            inference_kwargs["tools"] = []
            logger.info(
                "[TOOLS] (%s) tools_offered_to_inference count=0 (list_tools failed)",
                log_origin,
            )

    logger.info(
        "[INFERENCE] %s: Attempting Responses with Inference model: %s",
        log_origin,
        model,
    )
    try:
        model_info = get_model_info(model_key=model)
        endpoint = getattr(model_info, "endpoint", "unknown")
        logger.debug(
            "[INFERENCE] stage provider=%s model=%s endpoint=%s",
            provider,
            model,
            endpoint,
        )
    except Exception as exc:
        logger.debug(
            "[INFERENCE] stage provider=%s model=%s endpoint=unknown error=%s",
            provider,
            model,
            exc,
        )

    response = None
    try:
        stream_answer = bool(
            params.get("stream_answer", getattr(settings_obj, "stream_answer", False))
        )
        logger.info(
            "[STREAM] (%s) pass=initial requested=%s enable_tools=%s path=%s",
            log_origin,
            stream_answer,
            enable_tools,
            "stream" if stream_answer and not enable_tools else "non_stream",
        )
        if stream_answer and not enable_tools:
            response = consume_text_stream(
                responses_stream(
                    provider=provider,
                    model=model,
                    **inference_kwargs,
                ),
                on_delta=lambda delta: emit_stage(req_id, "Answer Delta", delta=delta),
            )
        else:
            response = responses_create(
                provider=provider,
                model=model,
                **inference_kwargs,
            )
        logger.debug(
            "[INFERENCE] Response from responses_create: type=%s",
            type(response),
        )
    except Exception as exc:
        logger.error(
            "[INFERENCE] responses_create failed with %s: %s...",
            type(exc).__name__,
            str(exc)[:200],
        )
        logger.debug(
            "[INFERENCE] Exception details: type=%s args=%s",
            type(exc),
            getattr(exc, "args", None),
        )
        if isinstance(exc, LLMError):
            logger.error(
                "[INFERENCE] Caught LLMError: kind=%s provider=%s message=%s...",
                getattr(exc, "kind", "None"),
                getattr(exc, "provider", "None"),
                str(exc)[:100],
            )
            if (getattr(exc, "kind", "") or "") == "rate_limit":
                early_response = build_rate_limit_response(
                    req_id=req_id,
                    metrics=metrics,
                    rewrite_display=rewrite_display,
                    stage_label="inference",
                    provider=str(getattr(exc, "provider", "") or "").strip()
                    or "the inference provider",
                    model=str(getattr(exc, "model", "") or "").strip()
                    or "(unspecified model)",
                    action="generate a response safely",
                )
                return InferenceStageResult(
                    response=None,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    early_response=early_response,
                )
            if (getattr(exc, "kind", "") or "") == "timeout" or "timeout" in type(exc).__name__.lower():
                early_response = build_timeout_response(
                    req_id=req_id,
                    metrics=metrics,
                    rewrite_display=rewrite_display,
                    stage_label="inference",
                    provider=str(getattr(exc, "provider", "") or "").strip()
                    or "the inference provider",
                    model=str(getattr(exc, "model", "") or "").strip()
                    or "(unspecified model)",
                    action="generate a response safely",
                )
                return InferenceStageResult(
                    response=None,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    early_response=early_response,
                )
            raise
        if "timeout" in type(exc).__name__.lower():
            early_response = build_timeout_response(
                req_id=req_id,
                metrics=metrics,
                rewrite_display=rewrite_display,
                stage_label="inference",
                provider=provider,
                model=model,
                action="generate a response safely",
            )
            return InferenceStageResult(
                response=None,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                early_response=early_response,
            )
        raise

    try:
        if response is not None:
            raw_response = getattr(response, "raw", response)
            logger.debug(
                "[INFERENCE] (%s) raw response: %r",
                log_origin,
                raw_response,
            )
    except Exception:
        pass

    usage = (
        extract_usage_from_responses(response, provider=provider)
        if response is not None
        else None
    )
    if usage:
        metrics.record_stage(
            "inference",
            model=model,
            usage=usage,
            model_key=(stage_model_keys or {}).get("inference"),
        )

    return InferenceStageResult(
        response=response,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )
