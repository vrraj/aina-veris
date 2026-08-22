import os

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.retrieval.config import clear_retrieval_config_cache, resolve_retrieval_specs


def test_retrieval_config_resolves_coverage_defaults():
    clear_retrieval_config_cache()
    specs = resolve_retrieval_specs(
        domain="mountains",
        config_path="prompts/local_models_registry.yaml",
    )

    assert specs["coverage"] == {
        "enabled": True,
        "min_results_per_subquery": 1,
        "max_reserved": 4,
    }
