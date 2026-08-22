"""Load and validate retrieval configuration from unified YAML."""
import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from backend.core.config import settings

def get_retrieval_config_path() -> Path:
    """Get the path to the unified retrieval configuration file."""
    # Default path relative to project root
    default_path = Path(__file__).parent.parent.parent / "prompts" / "local_models_registry.yaml"
    
    # Check for environment variable override
    config_path = os.getenv("RETRIEVAL_CONFIG_PATH")
    if config_path:
        return Path(config_path)
    
    return default_path

def load_retrieval_config() -> Dict[str, Any]:
    """Load the unified retrieval configuration from YAML.
    
    Returns:
        Dictionary containing both runtime config and local model specs.
    
    Raises:
        FileNotFoundError: If the configuration file doesn't exist.
        yaml.YAMLError: If the YAML file is invalid.
    """
    config_path = get_retrieval_config_path()
    
    if not config_path.exists():
        raise FileNotFoundError(f"Retrieval configuration not found: {config_path}")
    
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    
    return config


def _expand_local_model_path(path: Any) -> str:
    cache_root = str(
        os.getenv("LOCAL_MODELS_CACHE_PATH")
        or getattr(settings, "local_models_cache_path", "~/models")
        or "~/models"
    )
    value = str(path)
    value = value.replace("${LOCAL_MODELS_CACHE_PATH}", cache_root)
    value = value.replace("$LOCAL_MODELS_CACHE_PATH", cache_root)
    return os.path.expandvars(os.path.expanduser(value))

def get_model_config(model_type: str) -> Dict[str, Any]:
    """Get configuration for a specific local model type.
    
    Args:
        model_type: One of "dense", "sparse", "late_interaction", or "reranker"
    
    Returns:
        Dictionary containing model configuration (name, dimensions, etc.)
    
    Raises:
        ValueError: If model_type is invalid or not found in config.
    """
    config = load_retrieval_config()
    local_models = config.get("local_models", {})
    
    if model_type not in local_models:
        raise ValueError(f"Model type '{model_type}' not found in local_models. Available types: {list(local_models.keys())}")
    
    model_config = local_models[model_type]
    
    # Expand cache_dir path if present
    if "cache_dir" in model_config:
        model_config["cache_dir"] = _expand_local_model_path(model_config["cache_dir"])
    
    # Check if model is enabled (for optional models like late_interaction and reranker)
    if "enabled" in model_config and not model_config["enabled"]:
        raise ValueError(f"Model type '{model_type}' is disabled in configuration.")
    
    return model_config


def get_model_config_by_key(model_key: str) -> Dict[str, Any]:
    """Resolve local model configuration by stable model_key.

    Args:
        model_key: Stable local key (e.g., "local:dense_default")

    Returns:
        Dictionary containing model configuration for the matching key.

    Raises:
        ValueError: If no matching local model key is found.
    """
    config = load_retrieval_config()
    local_models = config.get("local_models", {})
    if not isinstance(local_models, dict):
        raise ValueError("Invalid local_models configuration")

    mk = str(model_key or "").strip()
    for _, model_config in local_models.items():
        if not isinstance(model_config, dict):
            continue
        if str(model_config.get("model_key") or "").strip() == mk:
            resolved = dict(model_config)
            if "cache_dir" in resolved:
                resolved["cache_dir"] = _expand_local_model_path(resolved["cache_dir"])
            if "enabled" in resolved and not resolved["enabled"]:
                raise ValueError(f"Model key '{mk}' is disabled in configuration.")
            return resolved

    raise ValueError(f"Model key '{mk}' not found in local_models")


def resolve_local_model_cache_dir(model_config: Dict[str, Any], default_subdir: str = "fastembed_cache") -> str:
    """Resolve a local model cache directory from registry config and LOCAL_MODELS_CACHE_PATH."""
    cache_dir = model_config.get("cache_dir") if isinstance(model_config, dict) else None
    if not cache_dir:
        cache_root = (
            os.getenv("LOCAL_MODELS_CACHE_PATH")
            or getattr(settings, "local_models_cache_path", "~/models")
            or "~/models"
        )
        cache_dir = os.path.join(str(cache_root), default_subdir)
    return _expand_local_model_path(cache_dir)

def is_local_domain(domain: str) -> bool:
    """Check if a domain uses local (non-hosted) models.
    
    Args:
        domain: Domain name to check
    
    Returns:
        True if domain uses local models, False if hosted
    """
    domain_config = settings.DOMAIN_EMBEDDING_CONFIG.get(domain, {})
    return domain_config.get("model_type") == "local"

def get_domain_vector_type(domain: str) -> Optional[str]:
    """Get the vector type for a domain.
    
    Args:
        domain: Domain name to check
    
    Returns:
        None (unnamed), "dense", or "hybrid"
    """
    domain_config = settings.DOMAIN_EMBEDDING_CONFIG.get(domain, {})
    return domain_config.get("vector_type")
