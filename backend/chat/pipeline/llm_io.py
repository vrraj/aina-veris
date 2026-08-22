"""Shared LLM call and response-normalization helpers for chat pipeline stages."""

from typing import Any, Callable, Dict
import logging

from backend.llm.llm_client import generate, generate_stream

logger = logging.getLogger(__name__)


def responses_create(provider: str | None = None, **kwargs: Any):
    """Compatibility shim for LLM calls."""
    model = kwargs.get("model")
    if not model:
        raise ValueError("model is required for LLM calls")

    logger.debug("[LLM] responses_create called with model=%s provider=%s", model, provider)

    filtered_kwargs = {k: v for k, v in kwargs.items() if k not in ["model", "model_key"]}
    return generate(model_key=model, **filtered_kwargs)


def responses_stream(provider: str | None = None, **kwargs: Any):
    """Compatibility shim for streaming LLM calls."""
    model = kwargs.get("model")
    if not model:
        raise ValueError("model is required for LLM calls")
    filtered_kwargs = {k: v for k, v in kwargs.items() if k not in ["model", "model_key"]}
    return generate_stream(model_key=model, **filtered_kwargs)


def consume_text_stream(
    events: Any,
    *,
    on_delta: Callable[[str], Any] | None = None,
) -> Dict[str, Any]:
    """Consume adapter stream events into a completed-response-compatible dict."""
    text_parts: list[str] = []
    usage: Dict[str, Any] | None = None

    for event in events:
        event_type = (
            str(event.get("type") or "")
            if isinstance(event, dict)
            else str(getattr(event, "type", "") or "")
        )
        delta = event.get("delta") if isinstance(event, dict) else getattr(event, "delta", None)
        if not delta:
            try:
                choices = event.get("choices") if isinstance(event, dict) else getattr(event, "choices", None)
                if choices:
                    delta = getattr(choices[0].delta, "content", "") or ""
            except Exception:
                delta = ""

        if delta and (event_type == "response.output_text.delta" or not event_type):
            text = str(delta)
            text_parts.append(text)
            if on_delta is not None:
                on_delta(text)
        elif event_type == "response.completed":
            completed = event.get("response") if isinstance(event, dict) else getattr(event, "response", None)
            usage = extract_usage_from_responses(completed) if completed is not None else None

    return {"text": "".join(text_parts), "usage": usage or {}}


def extract_text_from_responses(resp: Any) -> str:
    """Return response text from a Responses-like object."""
    if isinstance(resp, dict) and resp.get("text"):
        try:
            return str(resp.get("text") or "")
        except Exception:
            pass

    base = getattr(resp, "adapter_response", resp)

    try:
        if hasattr(base, "output_text"):
            return str(base.output_text or "")
        if hasattr(base, "text"):
            return str(base.text or "")
        if isinstance(base, dict):
            return str(base.get("text") or base.get("output_text") or "")
    except Exception:
        pass

    for attr in ["output_text", "text", "content"]:
        try:
            val = getattr(resp, attr, None)
            if val:
                return str(val)
        except Exception:
            pass

    return ""


def extract_reasoning_from_responses(resp: Any) -> str | None:
    """Return reasoning text from a Responses-like object."""
    if isinstance(resp, dict) and resp.get("reasoning"):
        try:
            reasoning = str(resp.get("reasoning") or "")
            return reasoning if reasoning.strip() else None
        except Exception:
            pass

    base = getattr(resp, "adapter_response", resp)

    try:
        if hasattr(base, "reasoning"):
            reasoning = str(base.reasoning or "")
            return reasoning if reasoning.strip() else None
        if isinstance(base, dict):
            reasoning = str(base.get("reasoning") or "")
            return reasoning if reasoning.strip() else None
    except Exception:
        pass

    return None


def extract_usage_from_responses(resp: Any, provider: str = "openai") -> Dict[str, int] | None:
    """Extract canonical usage fields from a response object."""
    if resp is None:
        return None

    base = getattr(resp, "adapter_response", resp)

    try:
        if isinstance(resp, dict) and resp.get("usage"):
            usage = resp.get("usage") or {}
        elif hasattr(base, "usage"):
            usage = base.usage or {}
        elif isinstance(base, dict):
            usage = base.get("usage") or {}
        else:
            usage = {}
    except Exception:
        logger.exception("[USAGE DEBUG] failed to extract usage")
        usage = {}

    if not isinstance(usage, dict):
        try:
            usage = usage.model_dump()
        except Exception:
            try:
                usage = dict(usage)
            except Exception:
                usage = {}

    def _as_int(value: Any) -> int:
        try:
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str) and value.strip():
                return int(float(value))
        except Exception:
            return 0
        return 0

    def _nested_int(obj: Any, *path: str) -> int:
        current = obj
        for key in path:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = getattr(current, key, None)
            if current is None:
                return 0
        return _as_int(current)

    input_tokens = _as_int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("input_token_count")
        or usage.get("prompt_token_count")
    )
    output_tokens = _as_int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("output_token_count")
        or usage.get("candidates_token_count")
    )
    cached_tokens = _as_int(
        usage.get("cached_tokens")
        or usage.get("prompt_cached_tokens")
        or usage.get("cached_content_token_count")
    )
    reasoning_tokens = _as_int(usage.get("reasoning_tokens"))

    input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}
    cached_tokens = cached_tokens or _nested_int(input_details, "cached_tokens")
    reasoning_tokens = reasoning_tokens or _nested_int(output_details, "reasoning_tokens")

    total_tokens = _as_int(
        usage.get("total_tokens")
        or usage.get("total_token_count")
    )
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens

    completion_tokens = _as_int(usage.get("completion_tokens")) or output_tokens

    norm = {
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    if isinstance(usage, dict):
        for k in norm.keys():
            v = usage.get(k)
            try:
                if isinstance(v, (int, float)):
                    norm[k] = int(v)
            except Exception:
                continue

    return norm
