"""Chat Manager Module

Entry Point:
- handle_chat(payload: Dict) -> Dict: Main entry point for "statless" chat requests

Pipeline Initialization:
1. Initialize dependencies (DB, clients, tools)
2. Parse request payload
3. Set up metrics and logging

Chat Pipeline Stages (with History Integration):
1. Query Rewrite (optional) --> 2. Context (Document) Retrieval --> 3. Reranking --> 4. Context Summarization --> 5. Prompt Construction --> 6. LLM Inference --> 7. Tool Execution (if needed) --> 8. Final Response Generation


Conversation State:
- Full history maintained in memory
- Each turn appends both user message and assistant response
- Configurable history window size controls context length
- Automatic summarization of older messages
"""

from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)
import uuid
# NOTE: SSE stage emission is centralized in backend/stream_emit.py so chat_manager stays agnostic of registry details.
# Stream emission helpers (centralized in backend/stream_emit.py)
from backend.stream_emit import emit_stage, close_stream
from backend.core.config import settings
from backend.db import QdrantDB
from backend.chat.web_search import WebSearchClient
from backend.tools import list_tools, get_executor

from backend.llm.llm_client import LLMError
from backend.chat.chunked_history_manager import ChunkedHistoryManager
from backend.chat.pipeline.metrics import (
    Metrics,
    _get_convo_totals_for_namespace,
    _zero_convo_totals,
    clear_convo_totals_for_namespace,
)
from backend.chat.pipeline.contracts import PipelineExecutionContext
from backend.chat.pipeline.errors import build_rate_limit_response
from backend.chat.pipeline.summary import (
    SUMMARY_CACHE as _SUMMARY_CACHE,
    _evict_idle_namespaces,
    _touch_namespace,
)
from backend.chat.pipeline.stage_specs import resolve_stage_specs
from backend.chat.pipeline.stages.context_assembly import run_context_assembly_stage
from backend.chat.pipeline.stages.final_response import run_final_response_stage
from backend.chat.pipeline.stages.history import clear_chunk_manager_for_namespace, run_history_stage
from backend.chat.pipeline.stages.inference import run_inference_stage
from backend.chat.pipeline.stages.retrieval import run_retrieval_stage
from backend.chat.pipeline.stages.rerank import run_rerank_stage
from backend.chat.pipeline.stages.turn_resolution import run_turn_resolution_stage
from backend.chat.pipeline.stages.tool_execution import run_tool_execution_stage
from backend.chat.pipeline.stages.web_context import run_web_context_stage
from backend.chat.pipeline.tools import (
    clear_tool_registry_cache,
)
from backend.markdown_render import render_markdown_to_html

_WEB_SEARCH_CLIENT: WebSearchClient | None = None


def _get_web_search_client() -> WebSearchClient:
    global _WEB_SEARCH_CLIENT
    if _WEB_SEARCH_CLIENT is None:
        _WEB_SEARCH_CLIENT = WebSearchClient()
    return _WEB_SEARCH_CLIENT


class ChatManager:
    def reset_metrics(self):
        """Reset conversation totals and all cached chat state. Used by /chat/reset."""
        _zero_convo_totals()
        # Clear in-memory conversation state so "Clear chat" truly resets context.
        try:
            self.chat_history = []
        except Exception:
            pass
        # Clear per-instance summary cache used by ChatManager.chat()
        try:
            self._summary_cache.clear()
        except Exception:
            pass

    def __init__(self):
        self.qdrant_db = QdrantDB(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            collection_name=settings.collection_name
        )
        self.web_search = WebSearchClient()
        self.chat_history = []
        # Per-instance lightweight cache for history summaries reused within a turn
        self._summary_cache: Dict[str, str] = {}
        
        # Chunked history managers for different sessions
        self.chunk_managers: Dict[str, ChunkedHistoryManager] = {}

    def get_or_create_chunk_manager(self, session_id: str = "default") -> ChunkedHistoryManager:
        """Get or create a chunk manager for the given session."""
        if session_id not in self.chunk_managers:
            chunk_size_limit = getattr(settings, 'raw_tail_turns', 10)  # Use existing config
            self.chunk_managers[session_id] = ChunkedHistoryManager(
                chunk_size_limit=chunk_size_limit,
                session_id=session_id
            )
            logger.debug(f"[CHUNKED] Created new chunk manager for session {session_id}, chunk_size={chunk_size_limit}")
        return self.chunk_managers[session_id]

    def _get_session_id(self, params: Dict[str, Any] | None) -> str:
        """Extract session ID from params or use default."""
        if not params:
            return "default"
        return str(params.get("session_id", "default"))

    def _get_context(self, query: str, limit: int | None = None, score_threshold: float | None = None) -> List[Dict]:
        """Get relevant context from QdrantDB"""
        #logger.debug("Searching Qdrant for query: %s", query)
        try:
            limit = limit or int(settings.top_k)
            score_threshold = float(score_threshold if score_threshold is not None else settings.score_threshold)
            logger.debug("Qdrant search using limit=%s, score_threshold=%s", limit, score_threshold)
            # Use HNSW for faster search and Exact = False for faster search
            results = self.qdrant_db.search_similar(
                query=query,
                limit=int(limit),
                score_threshold=float(score_threshold),
                with_vectors=False,
                with_payload=True,
                exact=getattr(settings, "exact_match", False),
            )
            logger.debug("Qdrant search returned %d results", len(results))
            if results:
                logger.debug("First result score: %s", results[0].get('score', 'N/A'))
            return results
        except Exception as e:
            logger.exception("Error in _get_context: %s", e)
            return []

    def _get_web_context(self, query: str, existing_context: List[Dict]) -> List[Dict]:
        """Get additional context from web search"""
        return self.web_search.get_additional_context(query, existing_context)

    def chat(self, message: str, context: List[Dict], use_web_search: bool | None = None, params: Dict[str, Any] | None = None) -> Dict:
        """
        Thin wrapper: delegate to run_pipeline (Option A).
        Maintains stateful history and returns answer + sources.
        """

        # Prefer caller-provided query_id (so SSE subscriber can pre-open /chat/stream/stages?query_id=...)
        _p = params or {}
        req_id = str(_p.get("query_id") or _p.get("request_id") or uuid.uuid4().hex[:8])
        logger.info("Starting chat in chat_manager.chat() [req_id=%s] [msg=%s]", req_id, message[:50])
        if use_web_search is None:
            use_web_search = bool(getattr(settings, "use_web_search", False))
        try:
            if isinstance(params, dict) and params.get("use_web_search") is not None:
                use_web_search = bool(params.get("use_web_search"))
        except Exception:
            pass
        logger.debug("Context length=%d use_web_search=%s", len(context), use_web_search)
        
        # Debug: Show what history we're using
        history_to_use = context if context is not None else self.chat_history
        logger.info("Using history: %d messages from %s", len(history_to_use), 
                   "session context" if context is not None else "ChatManager history")
        if history_to_use:
            logger.debug("History preview: %s", 
                        [{"role": msg.get("role", "unknown"), "content": msg.get("content", "")[:50] + "..."} 
                         for msg in history_to_use[-3:]])  # Show last 3 messages

        # Always use orchestrator; legacy inlined flow removed (kept in git history).
        # Derive namespace for token accounting (use session_id as conversation_id)
        try:
            _uid = str((params or {}).get("user_id") or "").strip()
            _session_id = str((params or {}).get("session_id") or "").strip()
            # For session-based chat, use session_id as namespace for proper token accounting
            session_namespace = f"session:{_session_id}" if _session_id else ""
        except Exception:
            session_namespace = ""

        try:
            deps = {
                "db": self.qdrant_db,
                "cache": self._summary_cache,
                "settings": settings,
                "list_tools": list_tools,
                "get_executor": get_executor,
                "get_web_context": (lambda q, existing: self._get_web_context(q, existing)) if use_web_search else (lambda q, existing: []),
                # Preserve previous stateful behavior: build a single prompt string (no tools)
                "style": "messages",
                "enable_tools": False,
                "enable_query_rewrite": bool(getattr(settings, "enable_query_rewrite", False)),
                "use_web_search": bool(use_web_search),
                "log_origin": "chat_manager.chat[orchestrator]",
                "request_id": req_id,
                "namespace": session_namespace,  # Add namespace for token accounting
            }
            # Use context parameter if provided, otherwise use self.chat_history
            # This allows session-based chat to work properly while maintaining backward compatibility
            history_to_use = context if context is not None else self.chat_history
            req = {"message": message, "history": history_to_use, "params": (params or {})}

            out = run_pipeline(deps=deps, req=req)
            answer_text = out.get("answer", "") or ""
            
            # Debug: Log what we got from orchestrator
            logger.info("DEBUG: orchestrator out keys: %s", list(out.keys()) if isinstance(out, dict) else "not a dict")
            logger.info("DEBUG: orchestrator out: %s", out)

            # Update stateful history to preserve conversation context
            self.chat_history.extend([
                {"role": "user", "content": message},
                {"role": "assistant", "content": answer_text},
            ])

            # Prepare response with all metrics
            response_dict = {
                "response": answer_text,
                "answer": answer_text,  # Add for consistency with handle_chat
                "sources": out.get("sources", []),
                "metrics": out.get("metrics", {"vectors_retrieved": 0}),
                "turn_metrics": out.get("turn_metrics", {}),
                "conversation_totals": out.get("conversation_totals", {}),
                "tools_used": out.get("tools_used", []),
                "rewrite_display": out.get("rewrite_display", {}),
                "query_plan_display": out.get("query_plan_display", {}),
            }
            
            logger.info("DEBUG: response_dict keys: %s", list(response_dict.keys()))
            logger.info("DEBUG: response_dict: %s", response_dict)
            
            return response_dict
        except Exception as e:
            logger.exception("Exception in chat: %s", e)
            err_text = f"I'm sorry, I encountered an error while processing your request: {str(e)}"
            # Best-effort: terminate SSE stage stream on errors so UI doesn't hang when using this stateful path.
            try:
                emit_stage(req_id, "Final Answer", final=True, finalContent=err_text)
            except Exception:
                pass
            try:
                emit_stage(req_id, "Done", final=True)
            except Exception:
                pass
            try:
                close_stream(req_id)
            except Exception:
                pass
            return {
                "response": err_text,
                "sources": []
            }
# --- debug helper ---

def _dbg(label: str, text: str) -> None:
    """Guarded debug logging with truncation, controlled by config flags.
    Only logs when settings.debug_verbose is True.
    """
    try:
        if settings.debug_verbose:
            maxc = int(settings.debug_log_truncate_chars)
            snippet = text if len(text) <= maxc else (text[:maxc] + "…")
            logger.debug("%s %s", label, snippet)
    except Exception:
        # Never let logging break flow
        pass



def _has_tool_results(tools_used: Any) -> bool:
    """True if tools were executed / tool results exist."""
    try:
        if not tools_used:
            return False
        if isinstance(tools_used, list):
            return len(tools_used) > 0
        if isinstance(tools_used, dict):
            return len(tools_used.keys()) > 0
        return True
    except Exception:
        return False


def _should_emit_no_supported_sources(sources: Any, tools_used: Any) -> bool:
    """Only emit NO_SUPPORTED_SOURCES when we have neither doc sources nor tool results."""
    try:
        has_sources = isinstance(sources, list) and len(sources) > 0
    except Exception:
        has_sources = bool(sources)
    return (not has_sources) and (not _has_tool_results(tools_used))

# --- Unified pipeline orchestrator (Option A) ---

def run_pipeline(*, deps: Dict[str, Any], req: Dict[str, Any]) -> Dict[str, Any]:
    """
    Unified pipeline:
    retrieve -> maybe_rerank -> summarize -> build prompt -> inference -> optional tools -> sources -> metrics

    deps:
      - db: QdrantDB-like (must support search_similar)
      - cache: dict-like for summaries
      - settings: Settings object
      - list_tools: callable() -> list of tools
      - get_executor: callable(name) -> tool executor
      - get_web_context: callable(query, existing_context) -> list (optional)
      - style: 'messages' | 'flat'
      - enable_tools: bool
      - enable_query_rewrite: bool
      - use_web_search: bool
      - log_origin: str (for logs)
    req:
      - message: str
      - history: list[{role, content}]
      - params: dict
    """
    settings_obj = deps["settings"]
    db = deps["db"]
    cache = deps.get("cache", {})
    style = deps.get("style", "flat")
    enable_tools = bool(deps.get("enable_tools", False))
    enable_query_rewrite = bool(deps.get("enable_query_rewrite", False))
    use_web_search = bool(deps.get("use_web_search", False))
    get_web_context_fn = deps.get("get_web_context") or (lambda q, existing: [])
    list_tools_fn = deps.get("list_tools", list_tools)
    get_executor_fn = deps.get("get_executor", get_executor)
    log_origin = str(deps.get("log_origin", "pipeline"))
    req_id = deps.get("request_id") or uuid.uuid4().hex[:8]
    log_origin = f"{log_origin}#{req_id}"
    # Option A: namespace for cache keying (may be empty for backward compat)
    namespace = str(deps.get("namespace", "") or "").strip()
    # For stateless paths that use the module-level summary cache, track last-seen
    # and evict idle namespaces based on a TTL. This is best-effort and does not
    # affect correctness; it only bounds process memory.
    try:
        if namespace and (cache is _SUMMARY_CACHE):
            _touch_namespace(namespace)
            _evict_idle_namespaces()
    except Exception:
        # Never let cache housekeeping break the main flow
        pass

    message: str = (req or {}).get("message") or ""
    history: List[Dict[str, str]] = (req or {}).get("history") or []
    params: Dict[str, Any] = (req or {}).get("params") or {}
    logger.info(
        "[STREAM] (%s) request stream_answer=%s enable_tools=%s use_tools=%s",
        log_origin,
        params.get("stream_answer"),
        params.get("enable_tools"),
        params.get("use_tools"),
    )

    # --- Model registry keys (cost-only) ---
    # Optional stable model aliases from params, used ONLY for accurate cost lookup.
    # Support both flat format (embedding_model_key) and nested format (model_keys.embedding)
    # Fallback to settings model keys if not provided in params
    _mk = lambda k: (str(params.get(k)).strip() or None) if params.get(k) is not None else None
    _stage_model_keys = {s: _mk(f"{s}_model_key") for s in ("embedding", "rewrite", "summary", "rerank", "inference", "tools_synth")}
    # Also check nested model_keys format from frontend
    model_keys_nested = params.get("model_keys") or {}
    for stage in ("embedding", "rewrite", "summary", "rerank", "inference", "tools_synth"):
        if model_keys_nested.get(stage) and not _stage_model_keys.get(stage):
            _stage_model_keys[stage] = str(model_keys_nested.get(stage)).strip() or None
    # Fallback to settings model keys if still None
    if not _stage_model_keys.get("embedding"):
        _stage_model_keys["embedding"] = str(getattr(settings_obj, "embedding_model_key", "openai:embed_small"))
    if not _stage_model_keys.get("inference"):
        _stage_model_keys["inference"] = str(getattr(settings_obj, "inference_model_key", "openai:gpt-4o-mini"))
    if not _stage_model_keys.get("rewrite"):
        _stage_model_keys["rewrite"] = str(getattr(settings_obj, "rewrite_model_key", "openai:gpt-4o-mini"))
    if not _stage_model_keys.get("rerank"):
        _stage_model_keys["rerank"] = str(getattr(settings_obj, "rerank_model_key", "openai:gpt-4o-mini"))
    if not _stage_model_keys.get("summary"):
        _stage_model_keys["summary"] = str(getattr(settings_obj, "summarizer_model_key", "openai:gpt-4o-mini"))
    _stage_model_keys["tools_synth"] = _stage_model_keys.get("tools_synth") or _stage_model_keys.get("inference")

    # Per-UI control for whether to append Sources: blocks and structured sources.
    # Mode is set by frontends (e.g. chat.js, chat-embed.js) via params.mode.
    try:
        mode = str(params.get("mode", "")).strip().lower() or "chat"
    except Exception:
        mode = "chat"
    try:
        if mode == "embed":
            display_sources = bool(getattr(settings_obj, "display_sources_for_embed", False))
        else:
            display_sources = bool(getattr(settings_obj, "display_sources_for_chat", True))
    except Exception:
        display_sources = True

    # Optional per-request override
    try:
        if isinstance(params, dict) and "show_sources" in params:
            _v = params.get("show_sources")
            if _v is not None:
                display_sources = bool(_v)
    except Exception:
        pass

    # Per-turn control for emitting intermediate processing stages to SSE.
    # Precedence: params.show_processing_steps (per turn) overrides settings_obj.show_processing_steps (global default).
    try:
        if "show_processing_steps" in params:
            show_processing_steps = bool(params.get("show_processing_steps"))
        else:
            show_processing_steps = bool(getattr(settings_obj, "show_processing_steps", True))
    except Exception:
        show_processing_steps = True

    # --- Stage resolver (Step 1): compute provider/model/kwargs per stage from existing settings ---
    # NOTE: This is read-only in this step (no behavior change). We compute it early so later
    # steps can pull from a single source instead of scattered getattr(...) calls.
    try:
        _prompt_input_hint: Any = [] if str(style) == "messages" else ""
        stage_specs = resolve_stage_specs(
            settings_obj=settings_obj,
            params=params,
            enable_tools=enable_tools,
            prompt_input=_prompt_input_hint,
            message=message,
            list_tools_fn=list_tools_fn,
        )
    except Exception:
        stage_specs = {}

    # UI-friendly summary of rewrite decision (always returned)
    rewrite_display: Dict[str, Any] = {
        "enabled": bool(enable_query_rewrite),
        "triggered": False,
        "accepted": False,
        "original": message,
    }
    query_plan_display: Dict[str, Any] = {
        "enabled": bool(params.get("split_compound_queries", False)),
        "is_compound": False,
        "original_query": message,
        "normalized_query": message,
        "queries": [message],
        "fusion_method": None,
        "rerank_mode": "single_query",
    }

    # Conversation totals should be scoped per namespace (conversation_id/tab/session)
    _totals_ref = _get_convo_totals_for_namespace(namespace)
    m = Metrics(settings_obj, _totals_ref)
    execution_context = PipelineExecutionContext(
        settings=settings_obj,
        params=params,
        stage_specs=stage_specs or {},
        req_id=req_id,
        log_origin=log_origin,
        show_processing_steps=show_processing_steps,
        metrics=m,
        stage_model_keys=_stage_model_keys,
        rewrite_display=rewrite_display,
        query_plan_display=query_plan_display,
    )
    # Diagnostics: show whether we're using the default accumulator vs a namespace-scoped one
    try:
        if namespace:
            logger.debug("[TOTALS] (%s) using namespace-scoped totals ns='%s'", log_origin, namespace)
        else:
            logger.warning("[TOTALS] (%s) namespace is empty -> using default totals accumulator", log_origin)
    except Exception:
        pass

    # --- Resolve retrieval knobs
    try:
        top_k = int(params.get("top_k") or getattr(settings_obj, "top_k", 8))
        score_threshold = float(params.get("score_threshold") or getattr(settings_obj, "score_threshold", 0.0))
    except Exception:
        top_k = int(getattr(settings_obj, "top_k", 8))
        score_threshold = float(getattr(settings_obj, "score_threshold", 0.0))

    resolution_stage = run_turn_resolution_stage(
        context=execution_context,
        message=message,
        history=history,
        cache=cache,
        namespace=namespace,
        enabled=enable_query_rewrite,
    )
    if resolution_stage.early_response is not None:
        return resolution_stage.early_response
    effective_query = resolution_stage.effective_query

    retrieval_stage = run_retrieval_stage(
        context=execution_context,
        db=db,
        effective_query=effective_query,
        top_k=top_k,
        score_threshold=score_threshold,
    )
    if retrieval_stage.early_response is not None:
        return retrieval_stage.early_response
    results = retrieval_stage.results
    skip_rerank = retrieval_stage.skip_rerank
    n = retrieval_stage.count
    kept = retrieval_stage.kept
    effective_query = retrieval_stage.effective_query or effective_query

    rerank_stage = run_rerank_stage(
        context=execution_context,
        results=results or [],
        skip_rerank=skip_rerank,
        effective_query=effective_query,
        ranking_context=retrieval_stage.ranking_context,
        debug_log=_dbg,
    )
    if rerank_stage.early_response is not None:
        return rerank_stage.early_response
    reranked = rerank_stage.reranked
    kept = rerank_stage.kept

    history_stage = run_history_stage(
        context=execution_context,
        history=history,
        cache=cache,
        namespace=namespace,
    )
    recent_block_str = history_stage.recent_block_str
    summary_text = history_stage.summary_text

    web_context = run_web_context_stage(
        context=execution_context,
        use_web_search=use_web_search,
        get_web_context=get_web_context_fn,
        effective_query=effective_query,
        retrieval_results=results or [],
    )

    context_stage = run_context_assembly_stage(
        context=execution_context,
        reranked=reranked or [],
        kept=kept,
        web_context=web_context,
        recent_block_str=recent_block_str,
        summary_text=summary_text,
        message=message,
        style=style,
        enable_tools=enable_tools,
        debug_log=_dbg,
    )
    context_items = context_stage.context_items
    context_text = context_stage.context_text
    indexed_for_collapse = context_stage.indexed_sources
    sources_section = context_stage.sources_section
    prompt_domain = context_stage.prompt_domain
    prompt_input = context_stage.prompt_input

    inference_stage = run_inference_stage(
        context=execution_context,
        prompt_input=prompt_input,
        message=message,
        enable_tools=enable_tools,
        list_tools=list_tools_fn,
    )
    if inference_stage.early_response is not None:
        return inference_stage.early_response
    resp_inf = inference_stage.response
    temperature = inference_stage.temperature
    max_out = inference_stage.max_output_tokens

    tool_stage = run_tool_execution_stage(
        context=execution_context,
        enable_tools=enable_tools,
        prompt_input=prompt_input,
        inference_response=resp_inf,
        history=history,
        message=message,
        reranked=reranked or [],
        prompt_domain=prompt_domain,
        summary_text=summary_text,
        recent_block_str=recent_block_str,
        context_text=context_text,
        temperature=temperature,
        max_output_tokens=max_out,
        max_tool_calls=(
            int(params["max_tool_calls"])
            if params.get("max_tool_calls") is not None
            else None
        ),
        get_executor=get_executor_fn,
    )
    if tool_stage.early_response is not None:
        return tool_stage.early_response
    answer_override = tool_stage.answer_override
    tools_out = tool_stage.tools_used
    tool_artifacts = tool_stage.artifacts
    tool_sources = tool_stage.tool_sources

    return run_final_response_stage(
        context=execution_context,
        inference_response=resp_inf,
        answer_override=answer_override,
        reranked=reranked or [],
        web_context=web_context,
        context_items=context_items,
        indexed_sources=indexed_for_collapse,
        sources_section=sources_section,
        display_sources=display_sources,
        vectors_retrieved=n,
        tools_used=tools_out,
        tool_sources=tool_sources,
        artifacts=tool_artifacts,
    )

# --- end orchestrator ---

def handle_chat(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Thin handler: stateless path using orchestrator only.

    Expects payload with keys: message, params.top_k, params.score_threshold.
    Returns orchestrator answer and metrics; no legacy/flag paths.
    """
    # Extract request fields
    message: str = (payload or {}).get("message") or ""
    history: List[Dict[str, str]] = (payload or {}).get("history") or []
    params: Dict[str, Any] = (payload or {}).get("params") or {}

    # Optional server-side Markdown -> sanitized HTML rendering (feature-flagged)
    try:
        _render_html = bool((params or {}).get("render_html", False))
    except Exception:
        _render_html = False

    if not message:
        return {"answer": "", "metrics": {"vectors_retrieved": 0}}
    logger.info("Before generating req_id display query_id: %s", params.get("query_id"))
    req_id = params.get("query_id") or uuid.uuid4().hex[:8]
    logger.info("[REQ] handle_chat start stateless [req_id=%s]", req_id)

    # Determine rewrite toggle (param overrides settings)
    rewrite_enabled = bool((params or {}).get("enable_query_rewrite", getattr(settings, "enable_query_rewrite", False)))
    logger.debug("[REWRITE] (handle_chat#%s) enabled=%s", req_id, rewrite_enabled)
    try:
        _thr = params.get("rewrite_confidence_threshold")
        _tail = params.get("rewrite_tail_turns")
        if _thr is not None or _tail is not None:
            logger.debug("[REWRITE] (handle_chat#%s) overrides: threshold=%s tail_turns=%s", req_id, _thr, _tail)
    except Exception:
        pass

    # Resolve effective retrieval domain for this request.
    # Priority: params.active_domain -> params.prompt_domain -> settings.active_domain
    available_domains = getattr(settings, "DOMAIN_EMBEDDING_CONFIG", {}) or {}
    configured_default_domain = str(getattr(settings, "active_domain", "") or "").strip() or "default"
    requested_domain = str(
        (params or {}).get("active_domain")
        or (params or {}).get("prompt_domain")
        or configured_default_domain
    ).strip()
    effective_domain = requested_domain if requested_domain in available_domains else configured_default_domain
    domain_cfg = available_domains.get(effective_domain) or available_domains.get(configured_default_domain) or {}
    domain_collection = str(domain_cfg.get("collection_name") or settings.collection_name)
    domain_embedding_model_key = str(domain_cfg.get("embedding_model_key") or settings.embedding_model_key)

    # Fresh Qdrant client for stateless path using per-request domain routing
    db = QdrantDB(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        collection_name=domain_collection,
        embedding_model_key=domain_embedding_model_key,
    )

    try:
        logger.info(
            "[REQ %s] domain routing requested=%s effective=%s collection=%s embedding_model_key=%s",
            req_id,
            requested_domain,
            effective_domain,
            domain_collection,
            domain_embedding_model_key,
        )
    except Exception:
        pass

    # Determine tools flag (preserve prior behavior)
    enable_tools = False
    if isinstance(params, dict):
        if params.get("use_tools") is not None:
            enable_tools = bool(params.get("use_tools"))
        elif params.get("enable_tools") is not None:
            enable_tools = bool(params.get("enable_tools"))
        else:
            enable_tools = bool(getattr(settings, "enable_tools", False))
    else:
        enable_tools = bool(getattr(settings, "enable_tools", False))

    # Determine web search toggle (request overrides settings)
    try:
        _req_web = (payload or {}).get("use_web_search")
    except Exception:
        _req_web = None
    if _req_web is None:
        use_web_search = bool((params or {}).get("use_web_search", getattr(settings, "use_web_search", False)))
    else:
        use_web_search = bool(_req_web)

    # --- Derive a namespace for cache keying (non-breaking) ---
    try:
        _uid = str((params or {}).get("user_id") or "").strip()
        _cid = str((params or {}).get("conversation_id") or "").strip()
        _ns = (f"{_uid}:{_cid}" if _uid and _cid else (_cid or ""))
    except Exception:
        _ns = ""

    # Diagnostics: confirm namespace / conversation_id on every stateless request
    try:
        logger.info(
            "[REQ %s] ns='%s' user_id='%s' conversation_id='%s' query_id='%s'",
            req_id,
            _ns,
            (_uid or ""),
            (_cid or ""),
            (params.get("query_id") if isinstance(params, dict) else None),
        )
        if not _ns:
            logger.warning(
                "[REQ %s] EMPTY namespace -> using default CONVO_TOTALS (totals may appear to reset/collide)",
                req_id,
            )
    except Exception:
        pass

    deps = {
        "db": db,
        "cache": _SUMMARY_CACHE,
        "settings": settings,
        "list_tools": list_tools,
        "get_executor": get_executor,
        "get_web_context": (lambda q, existing: _get_web_search_client().get_additional_context(q, existing)) if use_web_search else (lambda q, existing: []),
        "style": "messages", # flat or messages (use messages for clear systemvs user roles separation)
        "enable_tools": bool(enable_tools),
        "enable_query_rewrite": bool(rewrite_enabled),
        "use_web_search": bool(use_web_search),
        "log_origin": "handle_chat",
        "request_id": req_id,
        "namespace": _ns,
    }
    req = {"message": message, "history": history, "params": params}

    try:
        # Log cache size before running the pipeline
        try:
            _pre_bytes = 0
            try:
                _pre_bytes = sum(len(v.encode('utf-8')) for v in _SUMMARY_CACHE.values())
            except Exception:
                _pre_bytes = sum(len(v) for v in _SUMMARY_CACHE.values())
            logger.info(
                "[REQ %s] _SUMMARY_CACHE size (pre): %d entries, %d bytes | user_id=%s conversation_id=%s",
                req_id, len(_SUMMARY_CACHE), _pre_bytes, (_uid or ""), (_cid or ""))
        except Exception:
            pass
        # Run the orchestrator (chat pipeline)
        logger.info("[PIPELINE] handle_chat running pipeline orchestrator")

        out = run_pipeline(deps=deps, req=req)

        # Log cache size after the pipeline completes
        try:
            _post_bytes = 0
            try:
                _post_bytes = sum(len(v.encode('utf-8')) for v in _SUMMARY_CACHE.values())
            except Exception:
                _post_bytes = sum(len(v) for v in _SUMMARY_CACHE.values())
            logger.info(
                "[REQ %s] _SUMMARY_CACHE size (post): %d entries, %d bytes | user_id=%s conversation_id=%s",
                req_id, len(_SUMMARY_CACHE), _post_bytes, (_uid or ""), (_cid or ""))
        except Exception:
            pass
        logger.info("[PIPELINE] handle_chat returning orchestrator output: %s", out.get("answer", ""))

        _answer_text = out.get("answer", "")
        try:
            logger.info("[PIPELINE] raw_markdown_answer (req_id=%s):\n%s", req_id, _answer_text)
        except Exception:
            pass
        _answer_html = ""
        logger.debug("RENDER HTML: %s", _render_html)
        if _render_html:
            try:
                _answer_html = render_markdown_to_html(_answer_text)
                logger.debug("RENDER HTML: %s", _answer_html)
                logger.debug("RENDER SUCCESS: %s", "SUCCESS")
            except Exception as e:
                logger.debug("Exception in render_markdown_to_html: %s", str(e))
                logger.debug("RENDER FAILED: %s", "FAILED")
                _answer_html = ""

        # Ensure the final message is properly formatted for the frontend
        if _render_html and _answer_html:
            emit_stage(req_id, "Final Answer", final=True, finalContent=_answer_text, finalHtml=_answer_html)
        else:
            emit_stage(req_id, "Final Answer", final=True, finalContent=_answer_text)
        # Send an explicit close message
        emit_stage(req_id, "Done", final=True)

        # Base response: preserve existing shape/keys for compatibility.
        resp: Dict[str, Any] = {
            "answer": _answer_text,
            "response": _answer_text,  # legacy compatibility for frontend expecting 'response'
            "metrics": out.get("metrics", {"vectors_retrieved": 0}),
            "turn_metrics": out.get("turn_metrics", {}),
            "conversation_totals": out.get("conversation_totals", {}),
            "tools_used": out.get("tools_used", []),
            "rewrite_display": out.get("rewrite_display", {}),
            "query_plan_display": out.get("query_plan_display", {}),
        }

        if _render_html and _answer_html:
            resp["answer_html"] = _answer_html

        # Non-breaking: only surface reasoning when present and non-empty.
        try:
            reasoning = out.get("reasoning") if isinstance(out, dict) else None
        except Exception:
            reasoning = None
        if reasoning:
            resp["reasoning"] = reasoning

        return resp
    except LLMError as e:
        # Fatal provider/config/LLM failure (e.g., missing API key, unsupported provider).
        # Surface a clear, structured error back to the caller while preserving
        # the existing SSE shutdown behavior.
        logger.error(
            "[PIPELINE] handle_chat fatal LLMError: provider=%s model=%s kind=%s code=%s msg=%s",
            getattr(e, "provider", None),
            getattr(e, "model", None),
            getattr(e, "kind", None),
            getattr(e, "code", None),
            str(e),
            exc_info=True,
        )
        err_text = str(e) or "LLM error during inference."
        try:
            emit_stage(req_id, "Final Answer", final=True, finalContent=err_text)
        except Exception:
            pass
        try:
            emit_stage(req_id, "Done", final=True)
        except Exception:
            pass
        try:
            close_stream(req_id)
        except Exception:
            pass
        return {
            "answer": err_text,
            "response": err_text,
            "metrics": {"vectors_retrieved": 0},
            "error": {
                "stage": "inference",
                "provider": getattr(e, "provider", None),
                "model": getattr(e, "model", None),
                "kind": getattr(e, "kind", None),
                "code": getattr(e, "code", None),
                "message": str(e) or "LLM error during inference.",
            },
        }
    except Exception as e:
        logger.exception("[PIPELINE] handle_chat orchestrator failed: %s", e)
        err_text = "Sorry, something went wrong."
        # Ensure SSE stage stream terminates on errors so the UI doesn't hang.
        try:
            emit_stage(req_id, "Final Answer", final=True, finalContent=err_text)
        except Exception:
            pass
        try:
            emit_stage(req_id, "Done", final=True)
        except Exception:
            pass
        try:
            close_stream(req_id)
        except Exception:
            pass
        return {
            "answer": err_text,
            "response": err_text,
            "metrics": {"vectors_retrieved": 0},
        }
