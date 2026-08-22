"""History summary stage and per-namespace chunk-manager state."""

from typing import Any, Dict, List
import logging
import time

from backend.core.config import settings
from backend.stream_emit import emit_stage
from backend.chat.chunked_history_manager import ChunkedHistoryManager
from backend.chat.simple_history_processor import SimpleHistoryProcessor
from backend.chat.utils import _get_param_int
from backend.chat.pipeline.contracts import HistoryStageResult, PipelineExecutionContext

logger = logging.getLogger(__name__)

_CHUNK_MANAGERS_BY_NS: Dict[str, ChunkedHistoryManager] = {}
_CHUNK_MANAGERS_LAST_SEEN: Dict[str, float] = {}


def _evict_idle_chunk_managers(now: float | None = None, max_idle_seconds: int | None = None) -> Dict[str, int]:
    try:
        current_time = float(now if now is not None else time.time())
    except Exception:
        current_time = time.time()
    try:
        ttl = int(max_idle_seconds) if max_idle_seconds is not None else int(getattr(settings, "chunk_manager_idle_ttl_seconds", 3600) or 3600)
    except Exception:
        ttl = 3600

    cleared = 0
    try:
        idle_keys = [k for k, ts in _CHUNK_MANAGERS_LAST_SEEN.items() if (current_time - float(ts or 0.0)) > ttl]
        for key in idle_keys:
            _CHUNK_MANAGERS_LAST_SEEN.pop(key, None)
            if key in _CHUNK_MANAGERS_BY_NS:
                _CHUNK_MANAGERS_BY_NS.pop(key, None)
                cleared += 1
    except Exception:
        pass
    return {"cleared": cleared, "active_namespaces": len(_CHUNK_MANAGERS_BY_NS)}


def _get_chunk_manager_for_namespace(namespace: str, settings_obj: Any) -> ChunkedHistoryManager:
    ns = str(namespace or "").strip()
    key = ns or ""
    try:
        _evict_idle_chunk_managers()
    except Exception:
        pass

    manager = _CHUNK_MANAGERS_BY_NS.get(key)
    if manager is None:
        try:
            chunk_size_limit = int(getattr(settings_obj, "raw_tail_turns", 10) or 10)
        except Exception:
            chunk_size_limit = 10
        manager = ChunkedHistoryManager(chunk_size_limit=chunk_size_limit, session_id=(ns or "default"))
        _CHUNK_MANAGERS_BY_NS[key] = manager

    try:
        _CHUNK_MANAGERS_LAST_SEEN[key] = time.time()
    except Exception:
        pass
    return manager


def clear_chunk_manager_for_namespace(namespace: str) -> Dict[str, Any]:
    ns = str(namespace or "").strip()
    key = ns or ""
    existed = key in _CHUNK_MANAGERS_BY_NS
    if existed:
        _CHUNK_MANAGERS_BY_NS.pop(key, None)
    _CHUNK_MANAGERS_LAST_SEEN.pop(key, None)
    return {"cleared": bool(existed), "namespace": key, "active_namespaces": len(_CHUNK_MANAGERS_BY_NS)}


def run_history_stage(
    *,
    context: PipelineExecutionContext,
    history: List[Dict[str, str]],
    cache: Dict[str, str],
    namespace: str,
) -> HistoryStageResult:
    """Prepare summarized and recent conversation text for inference."""
    settings_obj = context.settings
    params = context.params
    log_origin = context.log_origin
    req_id = context.req_id
    show_processing_steps = context.show_processing_steps
    try:
        logger.info("[PIPELINE] emit stage: Summarize Chat History")
        if show_processing_steps:
            emit_stage(req_id, "Summarize Chat History")
    except Exception:
        pass

    history_processor = SimpleHistoryProcessor(settings_obj)
    chunk_manager = _get_chunk_manager_for_namespace(namespace, settings_obj)

    try:
        effective_chunk_turns, _ = _get_param_int(
            params,
            ["raw_tail_turns"],
            int(getattr(settings_obj, "raw_tail_turns", 10) or 10),
        )
    except Exception:
        effective_chunk_turns = int(getattr(settings_obj, "raw_tail_turns", 10) or 10)

    try:
        if int(effective_chunk_turns or 0) > 0 and int(getattr(chunk_manager, "chunk_size_limit", 0) or 0) != int(effective_chunk_turns):
            chunk_manager.chunk_size_limit = int(effective_chunk_turns)
    except Exception:
        pass

    logger.debug("[CHUNKED] Using chunked history for namespace '%s'", namespace or "")

    enable_token_based = getattr(settings_obj, "enable_token_based_chunks", False)
    should_create_chunk = False
    if enable_token_based:
        token_limit = getattr(settings_obj, "raw_tail_token_limit", 4000)
        current_chunk = chunk_manager.get_current_chunk_messages(history)
        should_create_chunk = chunk_manager.should_create_new_chunk_by_tokens(current_chunk, token_limit)
        if should_create_chunk:
            logger.info("[CHUNKED] Creating new chunk for namespace '%s' (token limit reached: %s)", namespace or "", token_limit)
    else:
        should_create_chunk = chunk_manager.should_create_new_chunk()
        if should_create_chunk:
            logger.info("[CHUNKED] Creating new chunk for namespace '%s' (turn limit reached)", namespace or "")

    if should_create_chunk:
        success = chunk_manager.create_new_chunk(history, settings_obj, cache, namespace)
        if not success:
            logger.warning("[CHUNKED] Failed to create new chunk for namespace '%s', falling back to current chunk", namespace or "")

    recent_conversation, summary_text = chunk_manager.get_history_for_prompt(history)
    chunk_manager.increment_turn_count()

    recent_block_str = ""
    if recent_conversation:
        recent_block_str = history_processor.format_recent_conversation(
            recent_conversation,
            params,
            log_origin,
        )

    return HistoryStageResult(
        recent_block_str=recent_block_str,
        summary_text=summary_text,
    )
