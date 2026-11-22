from types import SimpleNamespace

import pytest

from app.services import ai
from app.services.ai import EmbeddingServiceError


def test_parse_embedding_response_orders_vectors_by_index() -> None:
    result = ai._parse_embedding_response(
        {
            "data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ]
        },
        2,
    )
    assert result == [[1.0, 0.0], [0.0, 1.0]]


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"data": [{"index": 0, "embedding": []}]},
        {"data": [{"index": 0, "embedding": [float("nan")]}]},
        {"data": [{"index": 1, "embedding": [1.0]}]},
        {"data": [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [2.0]}]},
    ],
)
def test_parse_embedding_response_rejects_incomplete_or_invalid_vectors(payload) -> None:
    with pytest.raises(EmbeddingServiceError):
        ai._parse_embedding_response(payload, 1 if len(payload["data"]) == 1 else 2)


def test_generate_embeddings_uses_nvidia_query_or_passage_mode(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"data": [{"index": 0, "embedding": [0.25, 0.75]}]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, *, json, headers):
            calls.append((url, json, headers))
            return FakeResponse()

    monkeypatch.setattr(ai, "get_settings", lambda: SimpleNamespace(
        nvidia_api_key="test-key",
        nvidia_base_url="https://example.test/v1",
        nvidia_embedding_model="nvidia/nemotron-3-embed-1b",
    ))
    monkeypatch.setattr(ai.httpx, "Client", FakeClient)

    vectors = ai.generate_embeddings(["turnover threshold"], input_type="query")

    assert vectors == [[0.25, 0.75]]
    assert calls[0][0] == "https://example.test/v1/embeddings"
    assert calls[0][1]["input_type"] == "query"
    assert calls[0][1]["model"] == "nvidia/nemotron-3-embed-1b"
    assert calls[0][2]["Authorization"] == "Bearer test-key"


def test_generate_embeddings_rejects_empty_batch() -> None:
    assert ai.generate_embeddings([], input_type="passage") == []


def test_generate_embeddings_rejects_invalid_input_type() -> None:
    with pytest.raises(ValueError):
        ai.generate_embeddings(["text"], input_type="invalid")
