import os
from dataclasses import FrozenInstanceError

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.chat.pipeline.contracts import PipelineExecutionContext
from backend.chat.pipeline.stages.final_response import run_final_response_stage
from backend.chat.pipeline.stages.inference import run_inference_stage
from backend.chat.pipeline.stages.rewrite import run_rewrite_stage
from backend.chat.pipeline.stages.tool_execution import run_tool_execution_stage
from backend.chat.pipeline.stages.tool_execution import _normalize_synthesized_answer
from backend.chat.pipeline.stages.web_context import run_web_context_stage
from backend.chat.rewrite_helpers import should_rewrite
from backend.chat.turn_resolution import normalize_turn_resolution


class FakeMetrics:
    def __init__(self):
        self.turn = {"stages": {}}
        self.finalized = False

    def finalize_turn(self):
        self.finalized = True

    def snapshot(self):
        return self.turn, {"tokens": {"conversation_total": 0}, "costs": {"conversation_total": 0.0}}

    def record_stage(self, *_args, **_kwargs):
        return None


def make_context(*, metrics=None, rewrite_display=None, show_processing_steps=False):
    return PipelineExecutionContext(
        settings=object(),
        params={},
        stage_specs={},
        req_id="test-request",
        log_origin="test",
        show_processing_steps=show_processing_steps,
        metrics=metrics or FakeMetrics(),
        stage_model_keys={},
        rewrite_display=rewrite_display or {"enabled": False},
    )


def test_execution_context_is_frozen_but_shares_turn_state():
    rewrite_display = {"accepted": False}
    context = make_context(rewrite_display=rewrite_display)

    rewrite_display["accepted"] = True

    assert context.rewrite_display["accepted"] is True
    with pytest.raises(FrozenInstanceError):
        context.req_id = "changed"


def test_tool_synthesis_marker_does_not_discard_supported_partial_answer():
    answer = _normalize_synthesized_answer(
        "Mount Kilimanjaro is in Tanzania.\n\n"
        "Weather is unavailable. NO_SUPPORTED_SOURCES"
    )

    assert answer == "Mount Kilimanjaro is in Tanzania.\n\nWeather is unavailable."


def test_tool_synthesis_refusal_falls_back_to_successful_tool_output(monkeypatch):
    context = make_context()
    object.__setattr__(
        context,
        "stage_specs",
        {"tools_synth": {"provider": "openai", "model": "openai:gpt-4o-mini", "kwargs": {}}},
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution.extract_tool_calls",
        lambda _response: [{"name": "tavily_search", "id": "call-1", "args": {}}],
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution._load_tool_registry",
        lambda _settings: {"tools_by_name": {}},
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution.resolve_tools_synth_prompt",
        lambda **_kwargs: type("Prompt", (), {"system_instruction": "system"})(),
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution.responses_create",
        lambda **_kwargs: {"text": "I couldn't find any information to answer this question. NO_SUPPORTED_SOURCES"},
    )

    result = run_tool_execution_stage(
        context=context,
        enable_tools=True,
        prompt_input=[{"role": "user", "content": "search"}],
        inference_response={"tool_calls": []},
        history=[],
        message="search for AI agents",
        reranked=[],
        prompt_domain="",
        summary_text="",
        recent_block_str="",
        context_text="",
        temperature=0.4,
        max_output_tokens=100,
        get_executor=lambda _name: lambda *_args, **_kwargs: "Tavily found AI agent results.",
    )

    assert "Tavily found AI agent results." in result.answer_override
    assert "I couldn't find" not in result.answer_override


def test_rewrite_disabled_path_keeps_original_query():
    result = run_rewrite_stage(
        context=make_context(),
        message="original question",
        history=[{"role": "user", "content": "earlier"}],
        cache={},
        namespace="session:test",
        enabled=False,
    )

    assert result.effective_query == "original question"
    assert result.early_response is None


def test_contextual_acknowledgement_triggers_turn_resolution():
    assert should_rewrite("sure") is True
    assert should_rewrite("Please do.") is True


def test_turn_resolution_normalizes_legacy_rewrite_result():
    result = normalize_turn_resolution(
        {
            "rewritten": "alternative routes and hiking tips for Mount Whitney",
            "changed": True,
            "confidence": 0.95,
            "ambiguous": False,
        },
        "sure",
    )

    assert result["intent"] == "retrieve"
    assert result["standalone_query"] == "alternative routes and hiking tips for Mount Whitney"


def test_ambiguous_acknowledgement_returns_model_clarification(monkeypatch):
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.turn_resolution.rewrite_query",
        lambda *_args, **_kwargs: {
            "intent": "clarify",
            "standalone_query": "sure",
            "rewritten": "sure",
            "changed": False,
            "confidence": 0.98,
            "ambiguous": True,
            "clarification_question": "Would you like the beginner route or the advanced route?",
            "options": ["beginner route", "advanced route"],
            "response": "",
            "reason": "The offered routes are mutually exclusive.",
            "_usage": None,
        },
    )

    result = run_rewrite_stage(
        context=make_context(rewrite_display={"enabled": True}),
        message="sure",
        history=[
            {
                "role": "assistant",
                "content": "Would you like the beginner route or the advanced route?",
            }
        ],
        cache={},
        namespace="session:test",
        enabled=True,
    )

    assert result.early_response is not None
    assert result.early_response["answer"] == (
        "Would you like the beginner route or the advanced route?"
    )
    assert result.effective_query == "sure"


def test_compatible_acknowledgement_becomes_retrieval_query(monkeypatch):
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.turn_resolution.rewrite_query",
        lambda *_args, **_kwargs: {
            "intent": "retrieve",
            "standalone_query": "alternative routes and hiking tips for Mount Whitney",
            "rewritten": "alternative routes and hiking tips for Mount Whitney",
            "changed": True,
            "confidence": 0.95,
            "ambiguous": False,
            "reason": "Accepted both compatible offered topics.",
            "_usage": None,
        },
    )

    result = run_rewrite_stage(
        context=make_context(rewrite_display={"enabled": True}),
        message="sure",
        history=[
            {
                "role": "assistant",
                "content": "Would you like alternative routes or hiking tips for Mount Whitney?",
            }
        ],
        cache={},
        namespace="session:test",
        enabled=True,
    )

    assert result.early_response is None
    assert result.effective_query == "alternative routes and hiking tips for Mount Whitney"


def test_low_confidence_acknowledgement_does_not_retrieve_literal_message(monkeypatch):
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.turn_resolution.rewrite_query",
        lambda *_args, **_kwargs: {
            "intent": "retrieve",
            "rewritten": "sure",
            "changed": False,
            "confidence": 0.4,
            "ambiguous": False,
            "reason": "Could not confidently resolve the prior offer.",
            "_usage": None,
        },
    )

    result = run_rewrite_stage(
        context=make_context(rewrite_display={"enabled": True}),
        message="sure",
        history=[{"role": "assistant", "content": "Would you like more information?"}],
        cache={},
        namespace="session:test",
        enabled=True,
    )

    assert result.early_response is not None
    assert result.early_response["answer"] == (
        "Could you clarify what you would like me to continue with?"
    )


def test_tool_execution_disabled_path_does_not_resolve_executor():
    executor_requested = False

    def get_executor(_name):
        nonlocal executor_requested
        executor_requested = True

    result = run_tool_execution_stage(
        context=make_context(),
        enable_tools=False,
        prompt_input=[],
        inference_response=None,
        history=[],
        message="hello",
        reranked=[],
        prompt_domain="",
        summary_text="",
        recent_block_str="",
        context_text="",
        temperature=0.7,
        max_output_tokens=100,
        get_executor=get_executor,
    )

    assert result.answer_override is None
    assert result.tools_used == []
    assert result.early_response is None
    assert executor_requested is False


def test_web_context_disabled_does_not_call_provider():
    provider_called = False

    def get_web_context(_query, _results):
        nonlocal provider_called
        provider_called = True
        return [{"url": "https://example.com"}]

    result = run_web_context_stage(
        context=make_context(),
        use_web_search=False,
        get_web_context=get_web_context,
        effective_query="query",
        retrieval_results=[],
    )

    assert result == []
    assert provider_called is False


def test_web_context_provider_failure_falls_back_to_empty():
    def get_web_context(_query, _results):
        raise RuntimeError("provider unavailable")

    result = run_web_context_stage(
        context=make_context(),
        use_web_search=True,
        get_web_context=get_web_context,
        effective_query="query",
        retrieval_results=[],
    )

    assert result == []


def test_inference_streams_deltas_and_assembles_response(monkeypatch):
    emitted = []
    context = make_context()
    object.__setattr__(context, "params", {"stream_answer": True})
    object.__setattr__(
        context,
        "stage_specs",
        {"inference": {"provider": "openai", "model": "openai:gpt-4o-mini", "kwargs": {}}},
    )

    monkeypatch.setattr(
        "backend.chat.pipeline.stages.inference.responses_stream",
        lambda **_kwargs: iter(
            [
                {"type": "response.output_text.delta", "delta": "Hello "},
                {"type": "response.output_text.delta", "delta": "world"},
                {"type": "response.completed", "response": {"usage": {}}},
            ]
        ),
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.inference.emit_stage",
        lambda query_id, stage, **extra: emitted.append((query_id, stage, extra)),
    )

    result = run_inference_stage(
        context=context,
        prompt_input="prompt",
        message="hello",
        enable_tools=False,
        list_tools=lambda: [],
    )

    assert result.response["text"] == "Hello world"
    assert [item[2]["delta"] for item in emitted if item[1] == "Answer Delta"] == [
        "Hello ",
        "world",
    ]


def test_inference_with_tools_keeps_non_streaming_path(monkeypatch):
    context = make_context()
    object.__setattr__(context, "params", {"stream_answer": True})
    object.__setattr__(
        context,
        "stage_specs",
        {"inference": {"provider": "openai", "model": "openai:gpt-4o-mini", "kwargs": {}}},
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.inference.responses_create",
        lambda **_kwargs: {"text": "non-streaming", "usage": {}},
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.inference.responses_stream",
        lambda **_kwargs: pytest.fail("streaming should be disabled when tools are enabled"),
    )

    result = run_inference_stage(
        context=context,
        prompt_input=[{"role": "user", "content": "prompt"}],
        message="hello",
        enable_tools=True,
        list_tools=lambda: [],
    )

    assert result.response["text"] == "non-streaming"


def test_inference_stream_accepts_openai_compatible_chunks(monkeypatch):
    class Delta:
        content = "Gemini-compatible"

    class Choice:
        delta = Delta()

    class Chunk:
        choices = [Choice()]

    emitted = []
    context = make_context()
    object.__setattr__(context, "params", {"stream_answer": True})
    object.__setattr__(
        context,
        "stage_specs",
        {"inference": {"provider": "gemini", "model": "gemini:test", "kwargs": {}}},
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.inference.responses_stream",
        lambda **_kwargs: iter([Chunk()]),
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.inference.emit_stage",
        lambda query_id, stage, **extra: emitted.append((stage, extra)),
    )

    result = run_inference_stage(
        context=context,
        prompt_input="prompt",
        message="hello",
        enable_tools=False,
        list_tools=lambda: [],
    )

    assert result.response["text"] == "Gemini-compatible"
    assert emitted[-1] == ("Answer Delta", {"delta": "Gemini-compatible"})


def test_tool_synthesis_streams_only_after_tool_execution(monkeypatch):
    emitted = []
    context = make_context()
    object.__setattr__(context, "params", {"stream_answer": True})
    object.__setattr__(
        context,
        "stage_specs",
        {
            "tools_synth": {
                "provider": "openai",
                "model": "openai:gpt-4o-mini",
                "kwargs": {},
            }
        },
    )
    object.__setattr__(
        context,
        "stage_model_keys",
        {"tools_synth": "openai:gpt-4o-mini"},
    )

    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution.extract_tool_calls",
        lambda _response: [{"name": "test_tool", "id": "call-1", "args": {}}],
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution._load_tool_registry",
        lambda _settings: {"tools_by_name": {}},
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution.resolve_tools_synth_prompt",
        lambda **_kwargs: type("Prompt", (), {"system_instruction": "system"})(),
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution.build_tools_synth_messages",
        lambda **_kwargs: [{"role": "user", "content": "synthesize"}],
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution._strip_svg_from_messages",
        lambda messages: (messages, 0),
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution.responses_stream",
        lambda **_kwargs: iter(
            [
                {"type": "response.output_text.delta", "delta": "Tool "},
                {"type": "response.output_text.delta", "delta": "answer"},
            ]
        ),
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution.responses_create",
        lambda **_kwargs: pytest.fail("tool synthesis should stream"),
    )
    monkeypatch.setattr(
        "backend.chat.pipeline.stages.tool_execution.emit_stage",
        lambda query_id, stage, **extra: emitted.append((stage, extra)),
    )

    result = run_tool_execution_stage(
        context=context,
        enable_tools=True,
        prompt_input=[{"role": "user", "content": "use tool"}],
        inference_response={"tool_calls": []},
        history=[],
        message="use tool",
        reranked=[],
        prompt_domain="",
        summary_text="",
        recent_block_str="",
        context_text="",
        temperature=0.4,
        max_output_tokens=100,
        get_executor=lambda _name: lambda *_args, **_kwargs: "tool output",
    )

    assert result.answer_override == "Tool answer"
    assert result.tools_used == ["local:test_tool"]
    assert [extra["delta"] for stage, extra in emitted if stage == "Answer Delta"] == [
        "Tool ",
        "answer",
    ]


def test_final_response_keeps_only_cited_document_and_web_sources():
    metrics = FakeMetrics()
    context = make_context(metrics=metrics, rewrite_display={"accepted": True})
    documents = [
        {"payload": {"text": "first", "url": "https://docs/first"}},
        {"payload": {"text": "second", "url": "https://docs/second"}},
    ]
    indexed_sources = [
        {"index": 1, "url": "https://docs/first", "section": "A", "subsection": "One"},
        {"index": 2, "url": "https://docs/second", "section": "B", "subsection": "Two"},
    ]
    web_context = [{"url": "https://web/first"}, {"url": "https://web/second"}]

    result = run_final_response_stage(
        context=context,
        inference_response={"text": "Use the second source [2] and web result [web-1].", "reasoning": "brief"},
        answer_override=None,
        reranked=documents,
        web_context=web_context,
        context_items=documents,
        indexed_sources=indexed_sources,
        sources_section="unused initial sources",
        display_sources=True,
        vectors_retrieved=2,
        tools_used=[],
    )

    assert result["sources"] == [documents[1], web_context[0]]
    assert "[2] https://docs/second" in result["answer"]
    assert "[web-1] https://web/first" in result["answer"]
    assert "https://docs/first" not in result["answer"]
    assert result["metrics"] == {"vectors_retrieved": 2}
    assert result["reasoning"] == "brief"
    assert metrics.finalized is True


def test_final_response_keeps_cited_tool_sources_without_retrieval_results():
    result = run_final_response_stage(
        context=make_context(),
        inference_response=None,
        answer_override="Tavily result [tool-1].",
        reranked=[],
        web_context=[],
        context_items=[],
        indexed_sources=[],
        sources_section="",
        display_sources=True,
        vectors_retrieved=0,
        tools_used=["mcp:tavily_search"],
        tool_sources=[
            {
                "title": "Example",
                "url": "https://example.com/article",
                "snippet": "Source snippet",
                "provider": "tavily",
            }
        ],
    )

    assert "[tool-1] https://example.com/article" in result["answer"]
    assert result["sources"][0]["url"] == "https://example.com/article"


def test_final_response_strips_unsupported_sources_sentinel():
    result = run_final_response_stage(
        context=make_context(),
        inference_response={"text": "I cannot answer from the context.\nNO_SUPPORTED_SOURCES"},
        answer_override=None,
        reranked=[{"payload": {"text": "document"}}],
        web_context=[],
        context_items=[{"payload": {"text": "document"}}],
        indexed_sources=[{"index": 1, "url": "https://docs/one", "section": "A", "subsection": "One"}],
        sources_section="\n<sources>Sources</sources>:\n[1] https://docs/one",
        display_sources=True,
        vectors_retrieved=1,
        tools_used=[],
    )

    assert result["answer"] == "I cannot answer from the context."
    assert result["sources"] == []


def test_final_response_preserves_partial_answer_followup_and_cited_source():
    document = {"payload": {"text": "supported trail facts"}}
    result = run_final_response_stage(
        context=make_context(),
        inference_response={
            "text": (
                "The Inca Trail includes supported facts from the retrieved context [1].\n"
                "NO_SUPPORTED_SOURCES\n\n"
                "Which matters more to you: difficulty or scenery?"
            )
        },
        answer_override=None,
        reranked=[document],
        web_context=[],
        context_items=[document],
        indexed_sources=[
            {
                "index": 1,
                "url": "https://docs/trails",
                "section": "Trails",
                "subsection": "Inca Trail",
            }
        ],
        sources_section="\n<sources>Sources</sources>:\n[1] https://docs/trails",
        display_sources=True,
        vectors_retrieved=1,
        tools_used=[],
    )

    assert "supported facts from the retrieved context [1]" in result["answer"]
    assert "Which matters more to you: difficulty or scenery?" in result["answer"]
    assert "NO_SUPPORTED_SOURCES" not in result["answer"]
    assert "https://docs/trails" in result["answer"]
    assert result["sources"] == [document]
