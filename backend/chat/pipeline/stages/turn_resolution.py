"""Conversation-aware turn resolution pipeline stage."""

from typing import Any, Dict, List
import json
import logging
import re

from backend.core.config import settings
from backend.llm.llm_client import LLMError
from backend.stream_emit import close_stream, emit_stage
from backend.chat.pipeline.contracts import (
    PipelineExecutionContext,
    TurnResolutionStageResult,
)
from backend.chat.pipeline.errors import build_rate_limit_response
from backend.chat.pipeline.llm_io import (
    extract_text_from_responses,
    extract_usage_from_responses,
    responses_create,
)
from backend.chat.pipeline.summary import _summarize_messages_with_cache
from backend.chat.pipeline.text_utils import strip_code_fences, strip_trailing_sources_block
from backend.chat.pipeline.token_budget import get_encoder_for_model
from backend.chat.prompt_registry import resolve_rewrite_prompt, render_full_payload
from backend.chat.rewrite_helpers import should_rewrite
from backend.chat.turn_resolution import normalize_turn_resolution
from backend.chat.utils import _get_param_int, split_history_for_prompt

logger = logging.getLogger(__name__)


def _get_param_float(
    params: Dict[str, Any] | None,
    keys: List[str],
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> tuple[float, str]:
    values = params or {}
    for key in keys:
        if key in values and values[key] is not None:
            try:
                value = float(values[key])
                if minimum is not None:
                    value = max(minimum, value)
                if maximum is not None:
                    value = min(maximum, value)
                return value, f"param:{key}"
            except Exception:
                continue
    try:
        value = float(default)
    except Exception:
        value = default if isinstance(default, float) else 0.0
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value, "settings"


def _clarification_options(
    tail_messages: List[Dict[str, str]],
    message: str,
) -> List[str]:
    user_messages = [
        item
        for item in (tail_messages or [])
        if (item.get("role") or "").lower() == "user"
    ]
    if message:
        user_messages.append({"role": "user", "content": message})
    user_messages = user_messages[-2:]

    pattern = re.compile(
        r"\b([A-Z][A-Za-z0-9+/-]*(?:\s+[A-Z][A-Za-z0-9+/-]*){1,2})\b"
    )
    stopwords = {
        "the", "this", "that", "it", "weather", "climate", "sources", "section",
        "lead", "edit", "n/a", "today", "now", "summer", "winter", "spring",
        "autumn", "fall", "jan", "feb", "mar", "apr", "may", "jun", "jul",
        "aug", "sep", "sept", "oct", "nov", "dec", "january", "february",
        "march", "april", "june", "july", "august", "september", "october",
        "november", "december",
    }
    seen: set[str] = set()
    options: List[str] = []
    for item in reversed(user_messages):
        for candidate in pattern.findall(item.get("content") or ""):
            display = re.sub(r"^[Tt]he\s+", "", candidate.strip())
            display = re.sub(r"[\s\-–—,:;.!?]+$", "", display)
            key = display.lower()
            first_token = display.split()[0].lower() if display else ""
            if (
                key in seen
                or len(display.split()) < 2
                or key in stopwords
                or first_token in stopwords
            ):
                continue
            seen.add(key)
            options.append(display)
            if len(options) >= 5:
                break
        if len(options) >= 5:
            break
    options.sort(key=lambda value: (-len(value.split()), value))
    return options[:3]


def _clarification_response(
    *,
    context: PipelineExecutionContext,
    reason: str,
    options: List[str],
    question: str = "",
) -> Dict[str, Any]:
    if question:
        answer = question
    elif len(options) == 2:
        answer = f"Quick clarifier — do you mean {options[0]} or {options[1]}?"
    elif len(options) >= 3:
        answer = (
            f"Quick clarifier — which do you mean: {options[0]}, "
            f"{options[1]}, or {options[2]}?"
        )
    else:
        answer = "Could you clarify what you would like me to continue with?"

    logger.info(
        "[CLARIFY] (%s) reason=%s options=%s",
        context.log_origin,
        reason,
        options,
    )
    try:
        emit_stage(
            context.req_id,
            "Direct Response" if reason == "converse" else "Clarification Needed",
            prompt=answer,
            options=options,
            reason=reason,
        )
    except Exception:
        pass
    try:
        close_stream(context.req_id)
    except Exception:
        pass
    try:
        context.metrics.finalize_turn()
        turn_metrics, conversation_snapshot = context.metrics.snapshot()
    except Exception:
        turn_metrics = context.metrics.turn
        conversation_snapshot = {
            "tokens": {
                "embedding": 0,
                "llm_input": 0,
                "llm_output": 0,
                "conversation_total": 0,
            },
            "costs": {"conversation_total": 0.0},
        }
    return {
        "answer": answer,
        "sources": [],
        "turn_metrics": turn_metrics,
        "conversation_totals": conversation_snapshot,
        "metrics": {"vectors_retrieved": 0},
        "tools_used": [],
        "rewrite_display": context.rewrite_display,
    }


def rewrite_query(
    tail_messages: List[Dict[str, str]] | None,
    summary_text: str,
    message: str,
    prompt_domain: str = "",
    log_prefix: str = "[REWRITE]",
    stage_spec: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Call the rewrite model to produce a self-contained query."""
    try:
        registry_path = str(getattr(settings, "inference_prompt_registry_path", "") or "").strip()
        rw_spec = resolve_rewrite_prompt(registry_path=registry_path, domain=(prompt_domain or "").strip())

        tail_lines: List[str] = []
        try:
            for m in tail_messages or []:
                role = str(m.get("role") or "user")
                content = str(m.get("content") or "")
                if role == "assistant":
                    try:
                        content = strip_trailing_sources_block(content)
                    except Exception:
                        pass
                tail_lines.append(f"{role}: {content}")
        except Exception:
            tail_lines = []
        recent_block_str = "\n".join(tail_lines)

        payload = render_full_payload(
            rw_spec.full_payload_template,
            variables={
                "summary_text": summary_text or "",
                "recent_block_str": (recent_block_str or "").strip(),
                "message": message or "",
            },
        )

        prompt = rw_spec.system_instruction + "\n\n" + payload
        try:
            rw_cfg = stage_spec or {}
            model_for_est = str(rw_cfg.get("model") or getattr(settings, "rewrite_model_key", "openai:gpt-4o-mini"))
            enc = get_encoder_for_model(model_for_est)
            len(enc.encode(prompt))
        except Exception:
            pass

        rw_cfg = stage_spec or {}
        provider = str(rw_cfg.get("provider") or "openai")
        model = str(rw_cfg.get("model") or getattr(settings, "rewrite_model_key", "openai:gpt-4o-mini"))
        kwargs = dict(rw_cfg.get("kwargs") or {})
        if not kwargs:
            kwargs = {
                "max_output_tokens": int(getattr(settings, "rewrite_max_output_tokens", 300)),
                "temperature": float(getattr(settings, "rewrite_temperature", 0.3)),
            }

        try:
            from backend.llm.llm_client import get_model_info

            model_info = get_model_info(model_key=model)
            endpoint = getattr(model_info, "endpoint", "unknown")
            logger.debug(
                "[REWRITE] stage provider=%s model=%s endpoint=%s stage_spec=%s",
                provider,
                model,
                endpoint,
                stage_spec,
            )
        except Exception as e:
            logger.debug(
                "[REWRITE] stage provider=%s model=%s endpoint=unknown error=%s stage_spec=%s",
                provider,
                model,
                e,
                stage_spec,
            )

        resp = responses_create(
            provider=provider,
            model=model,
            input=prompt,
            **kwargs,
        )
        usage = extract_usage_from_responses(resp, provider=provider)
        raw = strip_code_fences(extract_text_from_responses(resp).strip())

        try:
            if isinstance(usage, dict):
                pt = int(usage.get("input_tokens") or 0)
                ct = int(usage.get("output_tokens") or 0)
                ck = int(usage.get("cached_tokens") or 0)
                tt = int(usage.get("total_tokens") or (pt + ct + ck))
                logger.debug("%s usage input=%d cached=%d output=%d total=%d", log_prefix, pt, ck, ct, tt)
        except Exception:
            pass

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("%s Invalid JSON from LLM: %s. Response was: %r", log_prefix, e, raw[:200])
            logger.warning("%s This often happens when model ignores JSON schema instruction", log_prefix)
            logger.warning("%s Response may be truncated - checking max_output_tokens setting", log_prefix)

            retry_prompt = prompt + "\n\nIMPORTANT: You MUST return ONLY a complete JSON object. No conversational text. Ensure all fields are included."
            resp_retry = responses_create(
                provider=provider,
                model=model,
                input=retry_prompt,
                **kwargs,
            )
            raw_retry = strip_code_fences(extract_text_from_responses(resp_retry).strip())

            try:
                data = json.loads(raw_retry)
                logger.info("%s Retry successful, got valid JSON", log_prefix)
            except json.JSONDecodeError:
                logger.error("%s Retry also failed, using original query", log_prefix)
                data = {
                    "rewritten": message,
                    "changed": False,
                    "confidence": 0.0,
                    "ambiguous": True,
                    "reason": "JSON parsing failed, using original",
                }

        rewritten = str(data.get("rewritten", message) or message)
        changed = bool(data.get("changed", False))
        confidence = float(data.get("confidence", 0.0) or 0.0)
        ambiguous = bool(data.get("ambiguous", False))
        reason = str(data.get("reason", "") or "")
        return normalize_turn_resolution({
            "intent": data.get("intent"),
            "standalone_query": data.get("standalone_query"),
            "rewritten": rewritten,
            "changed": changed,
            "confidence": confidence,
            "ambiguous": ambiguous,
            "reason": reason,
            "clarification_question": data.get("clarification_question"),
            "options": data.get("options"),
            "response": data.get("response"),
            "_usage": usage,
        }, message)
    except LLMError as e:
        try:
            kind = getattr(e, "kind", "") or ""
            provider = getattr(e, "provider", "") or ""
            model = getattr(e, "model", "") or ""
        except Exception:
            kind = ""
            provider = ""
            model = ""

        if kind == "rate_limit":
            logger.warning(
                "%s provider rate limit in rewrite: provider=%s model=%s error=%s",
                log_prefix,
                provider,
                model,
                e,
                exc_info=True,
            )
            return {
                "rewritten": message,
                "changed": False,
                "confidence": 0.0,
                "ambiguous": False,
                "reason": "llm_rate_limit",
                "_usage": None,
                "_provider": provider,
                "_model": model,
            }

        logger.warning("[REWRITE] failed with LLMError; using original: %s", e, exc_info=True)
        return {
            "rewritten": message,
            "changed": False,
            "confidence": 0.0,
            "ambiguous": True,
            "reason": "rewrite_error_or_ambiguous",
            "_usage": None,
        }
    except Exception as e:
        logger.warning("[REWRITE] failed to parse/produce JSON: %s", e, exc_info=True)
        return {
            "rewritten": message,
            "changed": False,
            "confidence": 0.0,
            "ambiguous": True,
            "reason": "rewrite_error_or_ambiguous",
            "_usage": None,
        }


def run_turn_resolution_stage(
    *,
    context: PipelineExecutionContext,
    message: str,
    history: List[Dict[str, str]],
    cache: Dict[str, str],
    namespace: str,
    enabled: bool,
) -> TurnResolutionStageResult:
    """Resolve the latest turn or return an early response before retrieval."""
    if not (enabled and history):
        return TurnResolutionStageResult(effective_query=message)

    try:
        logger.info("[PIPELINE] emit stage: Turn Resolution")
        if context.show_processing_steps:
            emit_stage(context.req_id, "Turn Resolution")
    except Exception:
        pass

    try:
        if not should_rewrite(message):
            context.rewrite_display.update(
                {"triggered": False, "accepted": False, "reason": "heuristic_false"}
            )
            return TurnResolutionStageResult(effective_query=message)

        settings_obj = context.settings
        params = context.params
        rewrite_tail, source_tail = _get_param_int(
            params,
            ["rewrite_tail_turns"],
            getattr(settings_obj, "rewrite_tail_turns", 1),
            minimum=0,
        )
        rewrite_summary, source_summary = _get_param_int(
            params,
            ["rewrite_summary_turns"],
            getattr(settings_obj, "rewrite_summary_turns", 3),
            minimum=0,
        )
        threshold, source_threshold = _get_param_float(
            params,
            ["rewrite_confidence_threshold"],
            getattr(settings_obj, "rewrite_confidence_threshold", 0.6),
            minimum=0.0,
            maximum=0.99,
        )
        logger.debug(
            "[REWRITE PARAMS] (%s) enable=%s tail_turns=%d (%s) summary_turns=%d (%s) threshold=%.2f (%s)",
            context.log_origin,
            True,
            rewrite_tail,
            source_tail,
            rewrite_summary,
            source_summary,
            threshold,
            source_threshold,
        )

        to_summarize, tail_messages = split_history_for_prompt(
            history,
            max(0, int(rewrite_tail)),
            max(0, int(rewrite_summary)),
        )
        summary_text = ""
        if int(rewrite_summary) > 0 and to_summarize:
            summary_spec = context.stage_specs.get("summary") or {}
            try:
                summary_text, from_cache, usage = _summarize_messages_with_cache(
                    to_summarize,
                    cache,
                    tag=(f"{namespace}|rewrite" if namespace else "rewrite"),
                    model=getattr(
                        settings_obj,
                        "summarizer_model",
                        settings_obj.inference_model,
                    ),
                    temperature=float(
                        getattr(settings_obj, "summarizer_temperature", 0.3)
                    ),
                    max_input_tokens=int(
                        getattr(settings_obj, "summarizer_max_input_tokens", 512)
                    ),
                    max_output_tokens=int(
                        getattr(settings_obj, "summarizer_max_output_tokens", 128)
                    ),
                    log_prefix=f"[REWRITE] {context.log_origin}",
                    stage_spec=summary_spec,
                )
            except LLMError as exc:
                if (getattr(exc, "kind", "") or "") == "rate_limit":
                    return TurnResolutionStageResult(
                        effective_query=message,
                        early_response=build_rate_limit_response(
                            req_id=context.req_id,
                            metrics=context.metrics,
                            rewrite_display=context.rewrite_display,
                            stage_label="summarizer",
                            provider=str(getattr(exc, "provider", "") or "").strip()
                            or "the summarizer provider",
                            model=str(getattr(exc, "model", "") or "").strip()
                            or "(unspecified summarizer model)",
                            action="prepare the query rewrite safely",
                        ),
                    )
                raise

            if not from_cache and usage:
                context.metrics.record_stage(
                    "summary",
                    model=str(
                        summary_spec.get("model")
                        or getattr(
                            settings_obj,
                            "summarizer_model",
                            settings_obj.inference_model,
                        )
                    ),
                    usage=usage,
                    extra={"applied": False, "reason": "rewrite_pre_summary"},
                    model_key=context.stage_model_keys.get("summary"),
                )

        if not (tail_messages or summary_text):
            context.rewrite_display.update(
                {"triggered": False, "accepted": False, "reason": "no_history"}
            )
            return TurnResolutionStageResult(effective_query=message)

        prompt_domain = str(params.get("prompt_domain") or "").strip()
        if not prompt_domain:
            prompt_domain = str(
                getattr(settings_obj, "prompt_domain_default", "") or ""
            ).strip()
        rewrite_spec = context.stage_specs.get("rewrite") or {}
        rewrite = rewrite_query(
            tail_messages,
            summary_text,
            message,
            prompt_domain=prompt_domain,
            log_prefix=f"[REWRITE] {context.log_origin}",
            stage_spec=rewrite_spec,
        )
        usage = rewrite.get("_usage") if isinstance(rewrite, dict) else None
        if rewrite.get("reason") == "llm_rate_limit":
            return TurnResolutionStageResult(
                effective_query=message,
                early_response=build_rate_limit_response(
                    req_id=context.req_id,
                    metrics=context.metrics,
                    rewrite_display=context.rewrite_display,
                    stage_label="query rewrite",
                    provider=str(rewrite.get("_provider") or "").strip()
                    or "the rewrite provider",
                    model=str(rewrite.get("_model") or "").strip()
                    or "(unspecified model)",
                    action="safely rewrite your question",
                ),
            )

        intent = str(rewrite.get("intent") or "retrieve").strip().lower()
        accepted = (
            intent == "retrieve"
            and bool(rewrite.get("changed"))
            and not rewrite.get("ambiguous")
            and float(rewrite.get("confidence", 0.0) or 0.0) >= float(threshold)
        )
        if usage:
            context.metrics.record_stage(
                "rewrite",
                model=str(
                    rewrite_spec.get("model")
                    or getattr(
                        settings_obj,
                        "rewrite_model",
                        settings_obj.inference_model,
                    )
                ),
                usage=usage,
                extra={"applied": True, "reason": "accepted" if accepted else "rejected"},
                model_key=context.stage_model_keys.get("rewrite"),
            )

        if accepted:
            effective_query = rewrite.get("rewritten") or message
            logger.info(
                "[REWRITE] (%s) accepted >=%s",
                context.log_origin,
                threshold,
            )
            context.rewrite_display.update(
                {
                    "triggered": True,
                    "accepted": True,
                    "rewritten": effective_query,
                    "confidence": float(rewrite.get("confidence", 0.0) or 0.0),
                    "threshold": float(threshold),
                    "ambiguous": bool(rewrite.get("ambiguous", False)),
                    "reason": str(rewrite.get("reason", "") or ""),
                    "changed": bool(rewrite.get("changed", False)),
                    "intent": intent,
                }
            )
            return TurnResolutionStageResult(effective_query=effective_query)

        context.rewrite_display.update(
            {
                "triggered": True,
                "accepted": False,
                "candidate": str(rewrite.get("rewritten", "") or ""),
                "confidence": float(rewrite.get("confidence", 0.0) or 0.0),
                "threshold": float(threshold),
                "ambiguous": bool(rewrite.get("ambiguous", False)),
                "reason": str(rewrite.get("reason", "") or ""),
                "changed": bool(rewrite.get("changed", False)),
                "intent": intent,
            }
        )
        if intent == "converse":
            response = str(rewrite.get("response") or "").strip()
            if not response:
                response = "You're welcome."
            return TurnResolutionStageResult(
                effective_query=message,
                early_response=_clarification_response(
                    context=context,
                    reason="converse",
                    options=[],
                    question=response,
                ),
            )
        if intent == "clarify":
            options = list(rewrite.get("options") or [])
            if not options:
                options = _clarification_options(tail_messages, message)
            return TurnResolutionStageResult(
                effective_query=message,
                early_response=_clarification_response(
                    context=context,
                    reason=str(rewrite.get("reason") or "ambiguous"),
                    options=options,
                    question=str(rewrite.get("clarification_question") or ""),
                ),
            )
        if intent == "retrieve" and not accepted:
            options = list(rewrite.get("options") or [])
            return TurnResolutionStageResult(
                effective_query=message,
                early_response=_clarification_response(
                    context=context,
                    reason=str(rewrite.get("reason") or "low_confidence"),
                    options=options,
                    question=str(rewrite.get("clarification_question") or ""),
                ),
            )
        if bool(rewrite.get("ambiguous", False)) and not bool(
            rewrite.get("changed", False)
        ):
            options = _clarification_options(tail_messages, message)
            logger.debug(
                "[CLARIFY] (%s) pool: from_user=%s",
                context.log_origin,
                options,
            )
            return TurnResolutionStageResult(
                effective_query=message,
                early_response=_clarification_response(
                    context=context,
                    reason="ambiguous",
                    options=options,
                ),
            )
    except Exception as exc:
        logger.warning(
            "[REWRITE] (%s) failed; using original: %s",
            context.log_origin,
            exc,
            exc_info=True,
        )
        context.rewrite_display.update(
            {"triggered": False, "accepted": False, "reason": "error"}
        )

    return TurnResolutionStageResult(effective_query=message)
