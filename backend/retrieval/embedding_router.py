from typing import List

from backend.retrieval.schemas import EmbeddingSpec, EmbeddingResult
from backend.retrieval.providers.hosted_embedding_provider import HostedEmbeddingProvider
from backend.retrieval.providers.fastembed_embedding_provider import FastEmbedEmbeddingProvider
from backend.retrieval.providers.bgem3_embedding_provider import Bgem3EmbeddingProvider


_FASTEMBED_PROVIDER = FastEmbedEmbeddingProvider()
_BGEM3_PROVIDER = Bgem3EmbeddingProvider()


class EmbeddingRouter:
    def __init__(self):
        self.providers = {
            "hosted": HostedEmbeddingProvider(),
            # Reuse the process-wide provider. This preserves ONNX model state
            # across routers created by indexing and retrieval code paths.
            "fastembed": _FASTEMBED_PROVIDER,
            "bgem3": _BGEM3_PROVIDER,
        }

    def embed(self, texts: List[str], spec: EmbeddingSpec) -> EmbeddingResult:
        provider = self.providers.get(spec.runtime)
        if provider is None:
            raise ValueError(f"Unsupported embedding runtime: {spec.runtime}")
        return provider.embed(texts, spec)
