from backend.llm import llm_client


def test_generate_stream_returns_adapter_iterator(monkeypatch):
    expected = iter([{"type": "response.output_text.delta", "delta": "hi"}])

    class FakeAdapter:
        def create(self, **kwargs):
            assert kwargs["stream"] is True
            assert kwargs["model"] == "openai:test"
            assert kwargs["input"] == "prompt"
            return expected

    monkeypatch.setattr(llm_client, "_get_adapter_for_model", lambda _model: FakeAdapter())

    result = llm_client.generate_stream(model_key="openai:test", input="prompt")

    assert result is expected
