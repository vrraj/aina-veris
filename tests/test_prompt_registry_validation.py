import copy
import os

import pytest
import yaml
from fastapi import HTTPException

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.main import _validate_prompt_registry_shape
from backend.chat.prompt_registry import clear_prompt_registry_cache, resolve_inference_prompt


def _registry():
    with open("prompts/prompt_registry.yaml", "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def test_prompt_registry_accepts_query_decomposition_stage():
    _validate_prompt_registry_shape(_registry())


def test_prompt_registry_requires_query_decomposition_stage():
    registry = copy.deepcopy(_registry())
    del registry["global_defaults"]["query_decomposition"]

    with pytest.raises(HTTPException, match="query_decomposition"):
        _validate_prompt_registry_shape(registry)


def test_backpacking_inference_prompt_requires_partial_answer_before_followup():
    path = "prompts/prompt_registry.yaml"
    clear_prompt_registry_cache(path)

    prompt = resolve_inference_prompt(registry_path=path, domain="backpacking")

    assert "Evidence sufficiency is granular, not all-or-nothing" in prompt.system_instruction
    assert "answer with all useful supported facts first" in prompt.system_instruction
    assert "Give the supported answer before asking a follow-up question" in prompt.system_instruction
    assert "provide a compact comparison using every supported dimension" in prompt.system_instruction
    assert "Do not request contact details unless the user explicitly asks" in prompt.system_instruction
