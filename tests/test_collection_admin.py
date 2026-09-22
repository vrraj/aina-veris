import os
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.services.collection_admin import list_collections, recreate_collection


class FakeClient:
    def __init__(self):
        self.recreate_args = None
        self.indexes = []
        self.info = SimpleNamespace(
            points_count=12,
            vectors_count=12,
            payload_schema={"url_lower": SimpleNamespace(params=None, data_type="keyword")},
            config=SimpleNamespace(
                params=SimpleNamespace(vectors=SimpleNamespace(size=1536), sparse_vectors=None, shard_number=1, sharding_method=None, replication_factor=1, write_consistency_factor=1, on_disk_payload=True, payload=None),
                hnsw_config=SimpleNamespace(model_dump=lambda **_: {}),
                optimizer_config=SimpleNamespace(model_dump=lambda **_: {}),
                wal_config=None, quantization_config=None, strict_mode_config=None, metadata=None,
            ),
        )

    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name="document_index")])

    def get_collection(self, name):
        assert name == "document_index"
        return self.info

    def recreate_collection(self, **kwargs):
        self.recreate_args = kwargs

    def create_payload_index(self, **kwargs):
        self.indexes.append(kwargs)


def test_list_collections_includes_counts_and_vector_configuration():
    collections = list_collections(FakeClient())
    assert collections[0]["name"] == "document_index"
    assert collections[0]["points_count"] == 12
    assert collections[0]["vector_config"] == "dense: 1536D"


def test_recreate_collection_preserves_vector_config_and_payload_indexes():
    client = FakeClient()
    result = recreate_collection(client, "document_index")
    assert result["deleted_points"] == 12
    assert client.recreate_args["vectors_config"].size == 1536
    assert client.indexes == [{"collection_name": "document_index", "field_name": "url_lower", "field_schema": "keyword"}]
