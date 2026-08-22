"""Token-budget helpers shared by chat pipeline stages."""

from typing import Any, Dict

import tiktoken

_ENCODER_CACHE: Dict[str, Any] = {}


def get_encoder_for_model(model_name: str):
    """Best-effort tiktoken encoder for a model; falls back to cl100k_base."""
    cache_key = str(model_name or "")
    if cache_key in _ENCODER_CACHE:
        return _ENCODER_CACHE[cache_key]
    try:
        enc = tiktoken.encoding_for_model(model_name)
    except Exception:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            class _Shim:
                def encode(self, s):
                    return list(s or "")

            enc = _Shim()
    _ENCODER_CACHE[cache_key] = enc
    return enc
