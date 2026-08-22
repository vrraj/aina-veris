"""Summary cache and pre-summary helpers for chat pipeline stages."""

from typing import Any, Dict, List, Set
from collections import defaultdict
import hashlib
import logging
import time

from backend.core.config import settings
from backend.llm.llm_client import LLMError
from backend.chat.pipeline.llm_io import (
    extract_text_from_responses as _extract_text_from_responses,
    extract_usage_from_responses as _extract_usage_from_responses,
    responses_create as _responses_create,
)
from backend.chat.pipeline.text_utils import strip_trailing_sources_block as _strip_trailing_sources_block
from backend.chat.pipeline.token_budget import get_encoder_for_model as _get_encoder_for_model
from backend.chat.prompt_registry import resolve_summary_prompt

logger = logging.getLogger(__name__)

SUMMARY_CACHE: Dict[str, str] = {}

# Index of namespace -> set of cache keys for precise clearing.
_SUMMARY_NS_INDEX: Dict[str, Set[str]] = defaultdict(set)
_SUMMARY_NS_LAST_SEEN: Dict[str, float] = {}


def _summary_cache_key(msgs: List[Dict[str, str]] | None, tag: str = "") -> str:
    """Create a stable cache key for a list of {role, content} messages."""
    items = msgs or []
    m = hashlib.sha1()
    m.update(tag.encode("utf-8"))
    for it in items:
        role = (it.get("role") or "").strip()
        content = (it.get("content") or "").strip()
        m.update(role.encode("utf-8"))
        m.update(b"\x1f")
        m.update(content.encode("utf-8"))
        m.update(b"\x1e")
    return m.hexdigest()


def _build_summary_prompt_with_budget(messages: List[Dict[str, str]], max_input_tokens: int | None, model_name: str, header: str | None = None) -> str:
    """
    Build a summary prompt that fits within `max_input_tokens` by trimming older lines first.
    Guarantees the most recent line is always included (clipped if necessary).
    
    NOTE: This function is currently ONLY used for rewrite stage pre-summarization.
    Other stages (inference, rerank) do not use this token budgeting mechanism.
    Chunked history mode bypasses this entirely and uses ChunkedHistoryManager.
    """
    header = (header if isinstance(header, str) and header else "Summarize the following conversation in a few sentences:\n\n")
    if not messages:
        return header

    # If no budget is set or <=0, include all lines verbatim.
    if max_input_tokens is None or int(max_input_tokens) <= 0:
        lines = [f"{m.get('role','user')}: {m.get('content','')}" for m in messages]
        return header + "\n".join(lines)

    enc = _get_encoder_for_model(model_name)

    def tok_len(s: str) -> int:
        try:
            return len(enc.encode(s))
        except Exception:
            return len(s or "")

    budget = int(max_input_tokens)
    used = tok_len(header)
    kept_rev: List[str] = []

    # Walk from newest to oldest so we never drop the freshest content.
    for m in reversed(messages):
        role = m.get("role", "user")
        content = m.get("content", "") or ""
        prefix = f"{role}: "
        line = prefix + content
        line_tokens = tok_len(line) + 1  # +1 for newline

        if not kept_rev:
            # Ensure newest line is included; clip content if it doesn't fit.
            if used + line_tokens > budget:
                # Compute remaining room for the line (excluding newline).
                remaining = max(0, budget - used - 1)
                if remaining <= tok_len(prefix):
                    # No room for content; keep a truncated prefix-only line.
                    line = prefix.strip()
                else:
                    room_for_content = remaining - tok_len(prefix)
                    try:
                        content_tokens = enc.encode(content)
                        if room_for_content < len(content_tokens):
                            # Keep leading tokens only and add ellipsis.
                            clipped_tokens = content_tokens[:max(0, room_for_content)]
                            clipped = enc.decode(clipped_tokens) if clipped_tokens else ""
                            line = f"{prefix}{clipped}…"
                    except Exception:
                        # Fallback: naive slice
                        line = prefix + (content[:max(0, room_for_content)] + "…")
                line_tokens = tok_len(line) + 1
            kept_rev.append(line)
            used += line_tokens
        else:
            # For older lines, only include if they fully fit; otherwise stop.
            if used + line_tokens <= budget:
                kept_rev.append(line)
                used += line_tokens
            else:
                break

    kept = list(reversed(kept_rev))
    # One-line debug counter: kept lines vs total, plus token usage vs budget
    try:
        logger.debug("[SUMMARY] input_budget kept_lines=%d/%d used_tokens≈%d budget=%d", len(kept), len(messages), used, budget)
    except Exception:
        pass
    return header + "\n".join(kept)
# --- end local token-budget helpers ---

def _summarize_messages_with_cache(
    messages: List[Dict[str, str]],
    cache: Dict[str, str],
    *,
    tag: str = "",
    model: str | None = None,
    temperature: float | None = None,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    log_prefix: str = "[SUMMARY]",
    stage_spec: Dict[str, Any] | None = None,
    provider: str | None = None,
    prompt_domain: str = "",
) -> tuple[str, bool, Dict[str, int] | None]:
    """
    Summarize a slice of messages with a tiny prompt, caching by (messages, tag).

    Returns: (summary_text, from_cache, usage_dict_or_none)
    
    NOTE: This function is currently ONLY used for rewrite stage pre-summarization.
    The tag parameter is used to distinguish cache keys (e.g., 'rewrite' vs 'namespace|rewrite').
    Other stages do not use this summarization mechanism.
    """
    # Log current cache size for observability
    if logger.isEnabledFor(logging.DEBUG):
        try:
            total_bytes = 0
            try:
                total_bytes = sum(len(v.encode('utf-8')) for v in cache.values())
            except Exception:
                # Fallback to character count if encoding fails
                total_bytes = sum(len(v) for v in cache.values())
            logger.debug("%s Cache size: %d entries, %d bytes", log_prefix, len(cache), total_bytes)
        except Exception:
            pass
    try:
        if not messages:
            return "", True, None

        # Build a cleaned copy of messages so we do not mutate the original history.
        # For assistant messages, strip any trailing 'Sources:' block before summarizing.
        cleaned_messages: List[Dict[str, str]] = []
        stripped = 0
        try:
            for m in messages:
                role = m.get("role", "user")
                content = m.get("content", "") or ""
                if role == "assistant":
                    new_content = _strip_trailing_sources_block(content)
                    if new_content != content:
                        stripped += 1
                    content = new_content
                cleaned_messages.append({"role": role, "content": content})
            if stripped:
                logger.debug("%s stripped trailing Sources: blocks from %d assistant messages before summary", log_prefix, stripped)
        except Exception:
            # If anything goes wrong during cleanup, fall back to the original messages.
            cleaned_messages = [{"role": m.get("role", "user"), "content": m.get("content", "") or ""} for m in messages]

        key = _summary_cache_key(cleaned_messages, tag=tag)
        cached = cache.get(key)
        if cached is not None:
            logger.debug(f"{log_prefix} summary cache HIT ({tag}); len=%d", len(cached))
            return cached, True, None

        _ss = stage_spec or {}
        _provider = str(_ss.get("provider") or "openai")
        _model = str(_ss.get("model") or model)

        registry_path = str(getattr(settings, "inference_prompt_registry_path", "") or "").strip()
        sum_spec = resolve_summary_prompt(registry_path=registry_path, domain=(prompt_domain or "").strip())
        header = (sum_spec.system_instruction or "").strip()
        if header:
            header = header + "\n\n"
        # Build the prompt using the effective model so token budgeting matches the selected provider/model.
        sum_prompt = _build_summary_prompt_with_budget(cleaned_messages, max_input_tokens, _model, header=header)
        logger.debug(f"{log_prefix} applied local input budget; prompt_len_chars=%d", len(sum_prompt))

        _call_kwargs: Dict[str, Any] = dict(_ss.get("kwargs") or {})
        if not _call_kwargs:
            _call_kwargs = {"temperature": float(temperature)}
            if max_output_tokens is not None:
                _call_kwargs["max_output_tokens"] = int(max_output_tokens)

        # Strip internal-only keys (e.g., _max_input_tokens) so they are not sent to providers.
        try:
            _call_kwargs = {k: v for k, v in _call_kwargs.items() if not str(k).startswith("_")}
        except Exception:
            # Best-effort; if filtering fails, fall back to original kwargs.
            pass

        # DEBUG: Log summary stage details
        logger.debug("[SUMMARY] stage provider=%s model=%s", _provider, _model)

        resp = _responses_create(
            provider=_provider,
            model=_model,
            input=sum_prompt,
            **_call_kwargs,
        )
        summary_text = _extract_text_from_responses(resp).strip()
        cache[key] = summary_text
        # Option A: record key under namespace (if tag includes namespace|...)
        try:
            if isinstance(tag, str) and '|' in tag:
                ns = tag.split('|', 1)[0].strip()
                if ns:
                    _SUMMARY_NS_INDEX[ns].add(key)
        except Exception:
            pass
        logger.debug(f"{log_prefix} summary cache MISS -> stored; len=%d", len(summary_text))
        usage = _extract_usage_from_responses(resp, provider=_provider)
        return summary_text, False, usage
    except LLMError:
        # Let LLMError (including rate_limit) propagate so outer callers can
        # apply consistent quota handling and surface clear messages.
        raise
    except Exception as e:
        logger.warning(f"{log_prefix} summary failed: %s", e, exc_info=True)
        return "", False, None
# --- end shared helpers ---


# --- Helper to clear summaries for a namespace ---
def clear_summaries_for_namespace(namespace: str) -> Dict[str, int]:
    """Clear cached summary entries for a given namespace.

    Returns a dict with counts for observability: {removed: int, remaining: int, reclaimed_bytes: int}
    """
    ns = str(namespace or "").strip()
    if not ns:
        return {"removed": 0, "remaining": len(SUMMARY_CACHE), "reclaimed_bytes": 0}
    keys = _SUMMARY_NS_INDEX.pop(ns, set())
    removed = 0
    reclaimed = 0
    try:
        for k in list(keys):
            v = SUMMARY_CACHE.pop(k, None)
            if isinstance(v, str):
                try:
                    reclaimed += len(v.encode('utf-8'))
                except Exception:
                    reclaimed += len(v)
            removed += 1
    except Exception:
        pass
    # Best-effort: also prune empty sets that may linger
    try:
        if ns in _SUMMARY_NS_INDEX and not _SUMMARY_NS_INDEX[ns]:
            _SUMMARY_NS_INDEX.pop(ns, None)
    except Exception:
        pass
    # Also drop last-seen entry for this namespace so it doesn't linger
    try:
        _SUMMARY_NS_LAST_SEEN.pop(ns, None)
    except Exception:
        pass
    return {"removed": removed, "remaining": len(SUMMARY_CACHE), "reclaimed_bytes": reclaimed}


# --- Namespace last-seen tracking and idle eviction ---
def _touch_namespace(namespace: str) -> None:
    """Record the last-seen time for a namespace in the module-level cache."""
    try:
        ns = str(namespace or "").strip()
        if not ns:
            return
        _SUMMARY_NS_LAST_SEEN[ns] = time.time()
    except Exception:
        # Best-effort only; never break the pipeline
        pass

def _evict_idle_namespaces(now: float | None = None, max_idle_seconds: int | None = None) -> Dict[str, int]:
    """
    Best-effort eviction of idle namespaces from the module-level summary cache.

    A namespace is considered idle if it has not been seen for more than `max_idle_seconds`.
    Defaults to 3600 seconds (1 hour) or `settings.summary_cache_idle_ttl_seconds` if present.

    Returns:
        {"namespaces_cleared": int, "summaries_removed": int, "reclaimed_bytes": int}
    """
    try:
        # Resolve TTL from settings with a safe default
        if max_idle_seconds is None:
            try:
                max_idle_seconds = int(getattr(settings, "summary_cache_idle_ttl_seconds", 3600))
            except Exception:
                max_idle_seconds = 3600
        if max_idle_seconds is None or max_idle_seconds <= 0:
            return {"namespaces_cleared": 0, "summaries_removed": 0, "reclaimed_bytes": 0}
        if now is None:
            now = time.time()
        cleared = 0
        removed_total = 0
        reclaimed_total = 0
        # Work on a snapshot so we can modify the dict while iterating
        for ns, last in list(_SUMMARY_NS_LAST_SEEN.items()):
            try:
                idle_for = now - float(last)
            except Exception:
                idle_for = max_idle_seconds + 1
            if idle_for > max_idle_seconds:
                stats = clear_summaries_for_namespace(ns)
                cleared += 1
                removed_total += int(stats.get("removed", 0) or 0)
                reclaimed_total += int(stats.get("reclaimed_bytes", 0) or 0)
                _SUMMARY_NS_LAST_SEEN.pop(ns, None)
        if cleared:
            try:
                logger.info(
                    "[SUMMARY] idle eviction cleared %d namespaces; removed=%d summaries reclaimed=%d bytes",
                    cleared,
                    removed_total,
                    reclaimed_total,
                )
            except Exception:
                pass
        return {"namespaces_cleared": cleared, "summaries_removed": removed_total, "reclaimed_bytes": reclaimed_total}
    except Exception:
        # Never let cache eviction break the main pipeline
        return {"namespaces_cleared": 0, "summaries_removed": 0, "reclaimed_bytes": 0}
