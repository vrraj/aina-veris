import os

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.chat.prompt_registry import (
    clear_prompt_registry_cache,
    render_full_payload,
    resolve_query_decomposition_prompt,
)


def test_query_decomposition_prompt_resolves_and_renders():
    path = "prompts/prompt_registry.yaml"
    clear_prompt_registry_cache(path)

    prompt = resolve_query_decomposition_prompt(registry_path=path, domain="")
    payload = render_full_payload(
        prompt.full_payload_template,
        variables={"query": "permits and weather", "max_queries": 3},
    )

    assert "multiple independently searchable information needs" in prompt.system_instruction
    assert "normalized_query" in prompt.system_instruction
    assert "Correct obvious spelling" in prompt.system_instruction
    assert "Return at most 3 queries" in payload
    assert "permits and weather" in payload
