from types import SimpleNamespace

from backend.chat.pipeline import stage_specs


def test_gemini_model_keys_select_gemini_for_all_generation_stages(monkeypatch):
    monkeypatch.setattr(
        stage_specs,
        "resolve_embedding_spec",
        lambda _settings: {"provider": "openai", "model": "embedding", "dimensions": 1536},
    )
    monkeypatch.setattr(stage_specs, "resolve_retrieval_specs", lambda **_kwargs: {})
    settings = SimpleNamespace(
        rewrite_model="openai:gpt-4o-mini",
        summarizer_model="openai:gpt-4o-mini",
        re_ranker_model="openai:gpt-4o-mini",
        inference_model_key="openai:gpt-4o",
        inference_provider="openai",
        rewrite_temperature=0.2,
        rewrite_max_output_tokens=128,
        summarizer_temperature=0.3,
        summarizer_max_input_tokens=512,
        summarizer_max_output_tokens=128,
        re_ranker_temperature=0.0,
        re_ranker_max_output_tokens=64,
        inference_temperature=0.2,
        inference_top_p=1.0,
        max_inference_output_tokens=800,
        tools_synth_max_output_tokens=800,
        inference_reasoning_effort="low",
        debug_thoughts=False,
        active_domain="",
        retrieval_config_path="",
    )

    specs = stage_specs.resolve_stage_specs(
        settings_obj=settings,
        params={
            "model_keys": {
                "rewrite": "gemini:flash",
                "summary": "gemini:flash",
                "rerank": "gemini:flash",
                "inference": "gemini:flash",
            }
        },
        enable_tools=False,
        prompt_input="",
        message="hello",
        list_tools_fn=lambda: [],
    )

    assert {name: specs[name]["provider"] for name in ("rewrite", "summary", "rerank", "inference")} == {
        "rewrite": "gemini",
        "summary": "gemini",
        "rerank": "gemini",
        "inference": "gemini",
    }
