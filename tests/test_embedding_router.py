import os

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.retrieval.embedding_router import EmbeddingRouter
from backend.retrieval.providers.fastembed_embedding_provider import FastEmbedEmbeddingProvider


def test_fastembed_provider_and_caches_are_process_shared():
    first_router = EmbeddingRouter()
    second_router = EmbeddingRouter()
    first_provider = first_router.providers["fastembed"]

    assert first_provider is second_router.providers["fastembed"]

    independently_created_provider = FastEmbedEmbeddingProvider()
    assert independently_created_provider._models is first_provider._models
    assert independently_created_provider._sparse_models is first_provider._sparse_models
