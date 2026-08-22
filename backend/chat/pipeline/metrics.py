"""Metrics and conversation totals for the chat pipeline."""

from typing import Any, Dict
import logging
import time

from backend.core.config import settings
from backend.embeddings.specs import resolve_embedding_spec
from backend.llm.llm_client import get_pricing_for_model
from backend.chat.pipeline.llm_io import extract_usage_from_responses as _extract_usage_from_responses

logger = logging.getLogger(__name__)

# ---- Conversation totals accumulator (per-namespace, module-level) ----
COST_BASIS = float(getattr(settings, "cost_basis_tokens", 1_000_000))

# Back-compat default (used when namespace is empty)
CONVO_TOTALS = {
    "tokens": {
        "embedding": 0,
        "llm_input": 0,      # prompt + cached tokens across stages
        "llm_output": 0,     # completion tokens across stages
        "conversation_total": 0,
    },
    "costs": {
        "embedding": 0.0,
        "llm_input": 0.0,
        "llm_output": 0.0,
        "total": 0.0,
        "conversation_total": 0.0,
    },
}

# Per-conversation/session totals, keyed by namespace (typically user_id:conversation_id or conversation_id)
_CONVO_TOTALS_BY_NS: Dict[str, Dict[str, Any]] = {}
_CONVO_TOTALS_LAST_SEEN: Dict[str, float] = {}

def _new_convo_totals() -> Dict[str, Any]:
    return {
        "tokens": {
            "embedding": 0,
            "llm_input": 0,
            "llm_output": 0,
            "conversation_total": 0,
        },
        "costs": {
            "embedding": 0.0,
            "llm_input": 0.0,
            "llm_output": 0.0,
            "total": 0.0,
            "conversation_total": 0.0,
        },
    }


def _zero_totals_dict(totals: Dict[str, Any]) -> None:
    """Best-effort reset of a totals dict to zeros."""
    try:
        totals["tokens"].update({
            "embedding": 0,
            "llm_input": 0,
            "llm_output": 0,
            "conversation_total": 0,
        })
        totals["costs"].update({
            "embedding": 0.0,
            "llm_input": 0.0,
            "llm_output": 0.0,
            "total": 0.0,
            "conversation_total": 0.0,
        })
    except Exception:
        # Best-effort only; never break the pipeline.
        pass


def _get_convo_totals_for_namespace(namespace: str) -> Dict[str, Any]:
    """Return a mutable totals dict scoped to `namespace` (conversation/session)."""
    ns = str(namespace or "").strip()
    if not ns:
        return CONVO_TOTALS
    try:
        _evict_idle_convo_totals()
    except Exception:
        pass
    existing = _CONVO_TOTALS_BY_NS.get(ns)
    if existing is None:
        existing = _new_convo_totals()
        _CONVO_TOTALS_BY_NS[ns] = existing
    try:
        _CONVO_TOTALS_LAST_SEEN[ns] = time.time()
    except Exception:
        pass
    return existing


def _evict_idle_convo_totals(now: float | None = None, max_idle_seconds: int | None = None) -> Dict[str, int]:
    try:
        _now = float(now if now is not None else time.time())
    except Exception:
        _now = time.time()
    try:
        _ttl = int(max_idle_seconds) if max_idle_seconds is not None else int(getattr(settings, "convo_totals_idle_ttl_seconds", 3600) or 3600)
    except Exception:
        _ttl = 3600

    cleared = 0
    try:
        idle_keys = [k for k, ts in _CONVO_TOTALS_LAST_SEEN.items() if (_now - float(ts or 0.0)) > _ttl]
        for k in idle_keys:
            _CONVO_TOTALS_LAST_SEEN.pop(k, None)
            if k in _CONVO_TOTALS_BY_NS:
                _CONVO_TOTALS_BY_NS.pop(k, None)
                cleared += 1
    except Exception:
        pass
    return {"cleared": cleared, "active_namespaces": len(_CONVO_TOTALS_BY_NS)}


def _zero_convo_totals() -> None:
    """Back-compat: reset the default (empty-namespace) accumulator."""
    _zero_totals_dict(CONVO_TOTALS)


def clear_convo_totals_for_namespace(namespace: str) -> Dict[str, Any]:
    """Clear totals for a specific namespace (conversation/session). Returns stats."""
    ns = str(namespace or "").strip()
    if not ns:
        _zero_convo_totals()
        return {"cleared": True, "namespace": "", "active_namespaces": len(_CONVO_TOTALS_BY_NS)}
    existed = ns in _CONVO_TOTALS_BY_NS
    if existed:
        _CONVO_TOTALS_BY_NS.pop(ns, None)
    _CONVO_TOTALS_LAST_SEEN.pop(ns, None)
    return {"cleared": bool(existed), "namespace": ns, "active_namespaces": len(_CONVO_TOTALS_BY_NS)}
 # ---- end accumulator ----



# --- Cost breakdown utility ---
def _compute_stage_cost(
    stage: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    model: str | None = None,
    provider: str | None = None,
    model_key: str | None = None,
) -> Dict[str, float]:
    """Return cost breakdown for a stage using per-million rates and COST_BASIS.

    Notes:
      * ``prompt_tokens`` is the canonical ``input_tokens`` (includes cached).
      * ``completion_tokens`` is the canonical ``output_tokens`` (includes reasoning).
      * ``cached_tokens`` is a subset of ``prompt_tokens`` and priced separately when a
        distinct cached-input rate is available in the model registry.


      * When a matching ``ModelInfo`` is found, per-million rates are taken from its
        ``pricing`` field and costs are computed by splitting input into non-cached and
        cached portions.
      * If the model cannot be resolved from the registry, this function returns zeros
        for all cost fields. In this deployment that should be treated as a
        configuration error (missing ModelInfo or pricing), not as a valid "free" run.

    NOTE: This function only affects cost math. It does NOT change any pipeline
    control flow or LLM behavior.
    """

    # Use model_key as primary identifier, provider is inferred
    pricing = get_pricing_for_model(model_key=model_key or model)

    if pricing is not None:
        try:
            # pricing is returned as a dict, use dict access instead of getattr
            in_rate = float(pricing.get("input_per_mm", 0.0) or 0.0)
            out_rate = float(pricing.get("output_per_mm", 0.0) or 0.0)
            cached_rate = float(pricing.get("cached_input_per_mm", 0.0) or 0.0)
        except Exception:
            in_rate = out_rate = cached_rate = 0.0

        # Split canonical input_tokens into non-cached and cached portions so that
        # only the non-cached portion is billed at the primary input rate.
        non_cached = max(int(prompt_tokens) - int(cached_tokens), 0)

        cost_prompt = (non_cached / COST_BASIS) * in_rate
        cost_cached = (cached_tokens / COST_BASIS) * cached_rate
        # `completion_tokens` here is the canonical `output_tokens` (includes reasoning).
        cost_completion = (completion_tokens / COST_BASIS) * out_rate
        total = cost_prompt + cost_cached + cost_completion
        return {
            "cost_prompt": round(cost_prompt, 10),
            "cost_cached": round(cost_cached, 10),
            "cost_completion": round(cost_completion, 10),
            "cost_total": round(total, 10),
        }

    # If model registry cannot be resolved, return zero costs rather than
    # guessing. In this deployment, this should be treated as a configuration
    # error (missing ModelInfo or pricing) and will be logged explicitly.
    try:
        _prov = str(provider or "").strip()
        _model_str = str(model or "").strip()
        _mk = str(model_key or "").strip()
        logger.error(
            "[METRICS] Missing pricing in model_registry for provider=%s model=%s model_key=%s; "
            "returning zero costs.",
            _prov,
            _model_str,
            _mk,
        )
    except Exception:
        # Never break the pipeline due to logging.
        pass

    return {
        "cost_prompt": 0.0,
        "cost_cached": 0.0,
        "cost_completion": 0.0,
        "cost_total": 0.0,
    }


# --- Centralized metrics helper (no integration yet) ---
class Metrics:
    """Centralizes stage usage parsing, cost math, and totals.

    Usage pattern (later steps):
        m = Metrics(settings, CONVO_TOTALS)
        m.record_stage("inference", model=settings.inference_model, usage=resp.usage)
        m.finalize_turn()
        turn_metrics, convo = m.snapshot()
    """
    def __init__(self, settings_obj, convo_totals_ref: Dict[str, Any]):
        self.settings = settings_obj
        # Resolve embedding spec once so we can report the concrete model name
        try:
            _emb_spec = resolve_embedding_spec(settings_obj)
            _emb_model_name = str(_emb_spec.get("model") or "text-embedding-3-small")
        except Exception:
            _emb_model_name = "text-embedding-3-small"
        # Exact shape expected by the UI
        self.turn: Dict[str, Any] = {
            "embedding": {"model": _emb_model_name, "input_tokens": 0, "costs": 0.0},
            "rerank": {"model": settings_obj.re_ranker_model, "input_tokens": 0, "output_tokens": 0, "candidates_reranked": 0, "costs": 0.0},
            "summary": {"model": settings_obj.summarizer_model, "applied": False, "reason": "", "input_tokens": 0, "output_tokens": 0, "costs": 0.0},
            "rewrite": {"model": getattr(settings_obj, "rewrite_model", settings_obj.inference_model), "applied": False, "reason": "", "input_tokens": 0, "output_tokens": 0, "costs": 0.0},
            # Inference pass #1 (initial answer / tool-planning)
            "inference": {
                "model": settings_obj.inference_model,
                "input_tokens": 0,
                "cached_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "cost_input": 0.0,
                "cost_cached": 0.0,
                "cost_output": 0.0,
                "cost_total": 0.0,
            },
            # Inference pass #2 (tool synthesis). Uses same model as inference for consistency.
            "inference_tools_synth": {
                "model": settings_obj.inference_model,
                "input_tokens": 0,
                "cached_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "cost_input": 0.0,
                "cost_cached": 0.0,
                "cost_output": 0.0,
                "cost_total": 0.0,
            },
            "totals": {"tokens": {"turn_total": 0}, "costs": {"turn_total": 0.0}},
        }
        # Module-level accumulator reference (shared per process)
        self.convo: Dict[str, Any] = convo_totals_ref

    # --- Helpers ---
    def _normalize_usage(self, resp_or_usage: Any, provider: str = "openai") -> Dict[str, int]:
        """Return dict with canonical usage fields (zeros if missing).

        Canonical fields: input_tokens, cached_tokens, output_tokens,
                          reasoning_tokens, completion_tokens, total_tokens.
        """
        try:
            # Accept either a full response object, a dict with nested usage, or a plain usage dict
            if hasattr(resp_or_usage, "usage"):
                # Full Responses API object
                u = _extract_usage_from_responses(resp_or_usage, provider=provider)
            elif isinstance(resp_or_usage, dict) and ("usage" in resp_or_usage):
                # Dict wrapping usage -> extract
                u = _extract_usage_from_responses(resp_or_usage, provider=provider)
            elif isinstance(resp_or_usage, dict) and (
                "input_tokens" in resp_or_usage
                or "output_tokens" in resp_or_usage
            ):
                # Already a canonical usage dict
                u = resp_or_usage
            else:
                u = None
        except Exception:
            u = None
        u = u or {}
        return {
            "input_tokens": int(u.get("input_tokens", 0) or 0),
            "cached_tokens": int(u.get("cached_tokens", 0) or 0),
            "output_tokens": int(u.get("output_tokens", 0) or 0),
            "reasoning_tokens": int(u.get("reasoning_tokens", 0) or 0),
            "completion_tokens": int(u.get("completion_tokens", 0) or 0),
            "total_tokens": int(u.get("total_tokens", 0) or 0),
        }

    def _cost(self, stage: str, model: str, pt: int, ct: int, cached: int, model_key: str | None = None) -> Dict[str, float]:
        # Delegate to existing utility for a single source of truth
        # NOTE: Costs are resolved via model_registry when possible.
        return _compute_stage_cost(
            stage,
            prompt_tokens=pt,
            completion_tokens=ct,
            cached_tokens=cached,
            model=model,
            model_key=model_key,
        )

    # --- Public API ---
    def record_stage(
        self,
        stage: str,
        pt: int | None = None,
        ct: int | None = None,
        cached: int | None = None,
        model: str | None = None,
        usage: Any | None = None,
        model_key: str | None = None,
        extra: Dict[str, Any] | None = None,
    ) -> None:
        """Record metrics for a pipeline stage.
        Either pass a `usage` (response or usage dict) or explicit pt/ct/cached counts.
        `extra` lets callers set fields like candidates_reranked/applied/reason.
        """
        if stage not in self.turn:
            return
        # Always stamp the model that ran
        self.turn[stage]["model"] = model

        # Extract canonical usage fields
        reasoning = 0
        if usage is not None and pt is None and ct is None and cached is None:
            u = self._normalize_usage(usage)
            pt, ct, cached, reasoning = u["input_tokens"], u["output_tokens"], u["cached_tokens"], u["reasoning_tokens"]
        pt = int(pt or 0)
        ct = int(ct or 0)
        cached = int(cached or 0)
        reasoning = int(reasoning or 0)

        if stage == "embedding":
            # input-only; we treat provided pt as input_tokens
            self.turn[stage]["input_tokens"] = pt
            c = self._cost("embedding", model, pt, 0, 0, model_key=model_key)
            self.turn[stage]["costs"] = c["cost_prompt"]
        elif stage == "rerank":
            # Use canonical input_tokens; cached is a subset and tracked separately via cost math.
            self.turn[stage]["input_tokens"] = pt
            self.turn[stage]["output_tokens"] = ct
            c = self._cost("rerank", model, pt, ct, cached, model_key=model_key)
            self.turn[stage]["costs"] = c["cost_total"]
        elif stage == "summary":
            self.turn[stage]["input_tokens"] = pt
            self.turn[stage]["output_tokens"] = ct
            c = self._cost("summary", model, pt, ct, cached, model_key=model_key)
            self.turn[stage]["costs"] = c["cost_total"]
        elif stage == "rewrite":
            self.turn[stage]["input_tokens"] = pt
            self.turn[stage]["output_tokens"] = ct
            c = self._cost("rewrite", model, pt, ct, cached, model_key=model_key)
            self.turn[stage]["costs"] = c["cost_total"]
        elif stage in ("inference", "inference_tools_synth"):
            # Accumulate tokens and costs across multiple inference calls in a single turn.
            prev_in = int(self.turn[stage].get("input_tokens") or 0)
            prev_ck = int(self.turn[stage].get("cached_tokens") or 0)
            prev_out = int(self.turn[stage].get("output_tokens") or 0)
            prev_reason = int(self.turn[stage].get("reasoning_tokens") or 0)

            in_total = prev_in + pt
            ck_total = prev_ck + cached
            out_total = prev_out + ct
            reason_total = prev_reason + reasoning

            self.turn[stage]["input_tokens"] = in_total
            self.turn[stage]["cached_tokens"] = ck_total
            self.turn[stage]["output_tokens"] = out_total
            self.turn[stage]["reasoning_tokens"] = reason_total

            # Cost for this specific call (still priced under the "inference" stage)
            c = self._cost("inference", model, pt, ct, cached, model_key=model_key)
            self.turn[stage]["cost_input"] = float(self.turn[stage].get("cost_input", 0.0)) + c["cost_prompt"]
            self.turn[stage]["cost_cached"] = float(self.turn[stage].get("cost_cached", 0.0)) + c["cost_cached"]
            self.turn[stage]["cost_output"] = float(self.turn[stage].get("cost_output", 0.0)) + c["cost_completion"]
            self.turn[stage]["cost_total"] = float(self.turn[stage].get("cost_total", 0.0)) + c["cost_total"]

        if extra:
            try:
                self.turn[stage].update(extra)
            except Exception:
                pass

    def finalize_turn(self) -> None:
        """Compute turn rollups and accumulate into conversation totals."""
        emb = int(self.turn["embedding"].get("input_tokens") or 0)
        rin = int(self.turn["rerank"].get("input_tokens") or 0)
        rout = int(self.turn["rerank"].get("output_tokens") or 0)
        sin = int(self.turn["summary"].get("input_tokens") or 0)
        sout = int(self.turn["summary"].get("output_tokens") or 0)
        rwin = int(self.turn["rewrite"].get("input_tokens") or 0)
        rwout = int(self.turn["rewrite"].get("output_tokens") or 0)

        # Inference pass #1
        ip1 = int(self.turn["inference"].get("input_tokens") or 0)
        ik1 = int(self.turn["inference"].get("cached_tokens") or 0)
        ic1 = int(self.turn["inference"].get("output_tokens") or 0)

        # Inference pass #2 (tool synthesis)
        ip2 = int(self.turn["inference_tools_synth"].get("input_tokens") or 0)
        ik2 = int(self.turn["inference_tools_synth"].get("cached_tokens") or 0)
        ic2 = int(self.turn["inference_tools_synth"].get("output_tokens") or 0)

        # Combined for totals/conversation metrics
        ip = ip1 + ip2
        ik = ik1 + ik2
        ic = ic1 + ic2

        # NOTE: cached tokens are a subset of prompt/input tokens; do NOT add them again to totals.
        total_tokens = emb + rin + rout + sin + sout + rwin + rwout + ip + ic
        self.turn["totals"]["tokens"]["turn_total"] = total_tokens

        total_cost = (
            float(self.turn["embedding"].get("costs") or 0.0)
            + float(self.turn["rerank"].get("costs") or 0.0)
            + float(self.turn["summary"].get("costs") or 0.0)
            + float(self.turn["rewrite"].get("costs") or 0.0)
            + float(self.turn["inference"].get("cost_total") or 0.0)
            + float(self.turn["inference_tools_synth"].get("cost_total") or 0.0)
        )
        self.turn["totals"]["costs"]["turn_total"] = round(total_cost, 10)

        # Accumulate into shared conversation totals
        try:
            self.convo["tokens"]["embedding"] += emb
            # NOTE: cached tokens are already included in stage input/prompt token counts; track them separately but don't double-count.
            self.convo["tokens"]["llm_input"] += (rin + sin + rwin + ip)
            self.convo["tokens"]["llm_output"] += (rout + sout + rwout + ic)
            self.convo["tokens"]["conversation_total"] += total_tokens
            self.convo["costs"]["conversation_total"] = round(float(self.convo["costs"].get("conversation_total", 0.0)) + total_cost, 10)
            logger.debug("[TOTALS] Metrics Finalize Turn turn_total=%d convo_total_now=%d" % (self.turn["totals"]["tokens"]["turn_total"], self.convo["tokens"]["conversation_total"]))
        except Exception:
            # Never let metrics break the answer path
            logger.error("[TOTALS] Metrics Finalize Turn Failure")
            pass

    def snapshot(self) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Return the current turn metrics and a frontend-aligned conversation totals snapshot."""
        convo_cost = 0.0
        if isinstance(self.convo, dict):
            convo_cost = float(self.convo["costs"].get("conversation_total", 0.0))
        convo_snapshot = {
            "tokens": self.convo.get("tokens", {"embedding": 0, "llm_input": 0, "llm_output": 0, "conversation_total": 0}),
            "costs": {"conversation_total": convo_cost},
        }
        return self.turn, convo_snapshot

    def reset_convo(self) -> None:
        try:
            _zero_convo_totals()
        except Exception:
            pass
# --- end Metrics helper ---

