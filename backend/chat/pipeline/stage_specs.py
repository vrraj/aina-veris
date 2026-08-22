"""Stage specification resolution for chat pipeline stages."""

from typing import Any, Dict, TypedDict
import logging

from backend.core.config import settings
from backend.embeddings.specs import resolve_embedding_spec
from backend.retrieval.config import resolve_retrieval_specs

logger = logging.getLogger(__name__)


class StageSpec(TypedDict):
    """Type definition for stage configuration dictionaries."""

    provider: str
    model: str
    kwargs: Dict[str, Any]


def resolve_stage_specs(
    *,
    settings_obj: Any,
    params: Dict[str, Any] | None,
    enable_tools: bool,
    prompt_input: Any,
    message: str,
    list_tools_fn: Any,
) -> Dict[str, StageSpec]:
    """Return provider/model/kwargs specs for each chat pipeline stage."""
    p = params or {}

    rerank_provider_override = str(p.get("rerank_provider") or "").strip()
    rerank_model_override = str(p.get("rerank_model") or "").strip()
    rewrite_provider_override = str(p.get("rewrite_provider") or "").strip()
    rewrite_model_override = str(p.get("rewrite_model") or "").strip()
    summary_provider_override = str(p.get("summary_provider") or "").strip()
    summary_model_override = str(p.get("summary_model") or "").strip()
    inference_provider_override = str(p.get("inference_provider") or "").strip()
    inference_model_override = str(p.get("inference_model") or "").strip()

    model_keys = p.get("model_keys") or {}
    logger.debug("[STAGE SPECS] model_keys from frontend: %s", model_keys)

    try:
        inference_model_key_override = str(model_keys.get("inference") or "").strip()
        rewrite_model_key_override = str(model_keys.get("rewrite") or "").strip()
        summary_model_key_override = str(model_keys.get("summary") or "").strip()
        rerank_model_key_override = str(model_keys.get("rerank") or "").strip()
        logger.debug(
            "[STAGE SPECS] extracted overrides: inference=%s rewrite=%s summary=%s rerank=%s",
            inference_model_key_override,
            rewrite_model_key_override,
            summary_model_key_override,
            rerank_model_key_override,
        )
    except Exception:
        inference_model_key_override = ""
        rewrite_model_key_override = ""
        summary_model_key_override = ""
        rerank_model_key_override = ""

    rewrite_model = getattr(settings_obj, "rewrite_model", getattr(settings_obj, "inference_model", ""))
    summarizer_model = getattr(settings_obj, "summarizer_model", getattr(settings_obj, "inference_model", ""))
    rerank_model = getattr(settings_obj, "re_ranker_model", getattr(settings_obj, "inference_model", ""))

    inference_model_key = getattr(settings_obj, "inference_model_key", "llm-adapter")
    effective_inference_model = (
        inference_model_key_override
        or inference_model_override
        or inference_model_key
    )

    inference_provider = getattr(settings_obj, "inference_provider", "openai")
    effective_inference_provider = inference_provider_override or inference_provider
    tools_synth_model = effective_inference_model

    active_domain = str(
        p.get("active_domain")
        or p.get("prompt_domain")
        or getattr(settings_obj, "active_domain", "")
        or ""
    ).strip()
    retrieval_specs = resolve_retrieval_specs(
        domain=active_domain,
        config_path=str(getattr(settings_obj, "retrieval_config_path", "") or "").strip() or None,
    )

    try:
        emb = resolve_embedding_spec(settings_obj)
    except Exception:
        emb = {"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536}

    emb_cfg = (retrieval_specs or {}).get("embedding") or {}
    emb_runtime = str(emb_cfg.get("runtime") or "hosted").strip() or "hosted"
    emb_provider = str(emb_cfg.get("provider") or (emb or {}).get("provider") or "openai").strip() or "openai"
    emb_model = str(emb_cfg.get("model") or (emb or {}).get("model") or "").strip()
    emb_dimensions = emb_cfg.get("dimensions", (emb or {}).get("dimensions"))
    emb_normalize = bool(emb_cfg.get("normalize", True))
    try:
        emb_batch_size = int(emb_cfg.get("batch_size", 32))
    except Exception:
        emb_batch_size = 32
    emb_device = emb_cfg.get("device")
    emb_extra = emb_cfg.get("extra") if isinstance(emb_cfg.get("extra"), dict) else {}

    rewrite_temp = float(getattr(settings_obj, "rewrite_temperature", 0.2))
    rewrite_max_out = int(getattr(settings_obj, "rewrite_max_output_tokens", getattr(settings_obj, "rewrite_max_tokens", 128)))

    summarizer_temp = float(getattr(settings_obj, "summarizer_temperature", 0.3))
    summarizer_max_in = int(getattr(settings_obj, "summarizer_max_input_tokens", 512))
    summarizer_max_out = int(getattr(settings_obj, "summarizer_max_output_tokens", 128))

    try:
        logger.debug(
            "[STAGE SPECS] summary provider=%s model=%s temp=%.3f max_in=%d max_out=%d",
            summary_provider_override or "openai",
            summary_model_override or summarizer_model,
            summarizer_temp,
            summarizer_max_in,
            summarizer_max_out,
        )
    except Exception:
        pass

    rerank_temp = float(getattr(settings_obj, "re_ranker_temperature", 0.0))
    rerank_max_out = int(getattr(settings_obj, "re_ranker_max_output_tokens", 64))

    rr_cfg = (retrieval_specs or {}).get("rerank") or {}
    rerank_runtime = str(rr_cfg.get("runtime") or "llm").strip() or "llm"
    rerank_enabled_cfg = bool(rr_cfg.get("enabled", True))
    rerank_provider_cfg = str(rr_cfg.get("provider") or "").strip()
    rerank_model_cfg = str(rr_cfg.get("model") or "").strip()
    rerank_top_n_cfg = rr_cfg.get("top_n")
    rerank_device_cfg = rr_cfg.get("device")
    try:
        rerank_batch_size_cfg = int(rr_cfg.get("batch_size", 16))
    except Exception:
        rerank_batch_size_cfg = 16
    rerank_extra_cfg = rr_cfg.get("extra") if isinstance(rr_cfg.get("extra"), dict) else {}

    inference_temp = float(getattr(settings_obj, "inference_temperature", 0.2))
    inference_top_p = float(getattr(settings_obj, "inference_top_p", 1.0))
    inference_max_out = int(getattr(settings_obj, "max_inference_output_tokens", 800))
    tools_synth_max_out = int(getattr(settings_obj, "tools_synth_max_output_tokens", inference_max_out))

    tools_kwargs: Dict[str, Any] = {}
    if enable_tools and isinstance(prompt_input, list):
        try:
            tools = list_tools_fn()

            def _is_web_search_requested(latest_user_msg: str) -> bool:
                if not latest_user_msg:
                    return False
                txt = latest_user_msg.lower()
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
                return any(k in txt for k in keys)

            if not _is_web_search_requested(message):
                tools = [t for t in tools if (t.get("name") or t.get("function", {}).get("name")) != "web_search"]
            tools_kwargs["tools"] = tools
        except Exception:
            tools_kwargs["tools"] = []

    try:
        logger.debug(
            "[STAGE SPECS] inference provider=%s model=%s max_out=%d | tools_synth max_out=%d",
            effective_inference_provider,
            effective_inference_model,
            inference_max_out,
            tools_synth_max_out,
        )
    except Exception:
        pass

    return {
        "embedding": {
            "provider": emb_provider,
            "model": emb_model,
            "runtime": emb_runtime,
            "kwargs": {
                "dimensions": emb_dimensions,
                "normalize": emb_normalize,
                "batch_size": emb_batch_size,
                "device": emb_device,
                **emb_extra,
            },
        },
        "rewrite": {
            "provider": rewrite_provider_override or "openai",
            "model": rewrite_model_key_override or rewrite_model_override or rewrite_model,
            "kwargs": {
                "temperature": rewrite_temp,
                "max_output_tokens": rewrite_max_out,
            },
        },
        "summary": {
            "provider": summary_provider_override or "openai",
            "model": summary_model_key_override or summary_model_override or summarizer_model,
            "kwargs": {
                "temperature": summarizer_temp,
                "max_output_tokens": summarizer_max_out,
                "_max_input_tokens": summarizer_max_in,
            },
        },
        "rerank": {
            "runtime": rerank_runtime,
            "provider": rerank_provider_override or rerank_provider_cfg or "openai",
            "model": rerank_model_key_override or rerank_model_override or rerank_model_cfg or rerank_model,
            "kwargs": {
                "enabled": rerank_enabled_cfg,
                "temperature": rerank_temp,
                "max_output_tokens": rerank_max_out,
                "top_n": rerank_top_n_cfg,
                "batch_size": rerank_batch_size_cfg,
                "device": rerank_device_cfg,
                **rerank_extra_cfg,
            },
        },
        "inference": {
            "provider": effective_inference_provider,
            "model": effective_inference_model,
            "kwargs": {
                "temperature": inference_temp,
                "top_p": inference_top_p,
                "max_output_tokens": inference_max_out,
                "reasoning_effort": getattr(settings, "inference_reasoning_effort", "low"),
                "debug_thoughts": getattr(settings, "debug_thoughts", True),
                **tools_kwargs,
            },
        },
        "tools_synth": {
            "provider": effective_inference_provider,
            "model": tools_synth_model,
            "kwargs": {
                "temperature": inference_temp,
                "max_output_tokens": tools_synth_max_out,
                "reasoning_effort": getattr(settings, "inference_reasoning_effort", "low"),
                "debug_thoughts": getattr(settings, "debug_thoughts", False),
            },
        },
    }
