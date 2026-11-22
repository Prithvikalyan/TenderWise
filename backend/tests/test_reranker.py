from types import SimpleNamespace

import pytest

from app.services import ai


class _FakeResponse:
    status_code = 200

    def __init__(self, body):
        self._body = body
        self.text = ""

    def json(self):
        return self._body


class _FakeClient:
    last_request = None
    body = {"rankings": [{"index": 1, "logit": 0.9}, {"index": 0, "logit": 0.2}]}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url, *, json, headers):
        self.last_request = {"url": url, "json": json, "headers": headers}
        return _FakeResponse(self.body)


def test_rerank_passages_uses_configured_nvidia_endpoint_and_orders_by_logit(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(ai.httpx, "Client", lambda *args, **kwargs: fake)
    monkeypatch.setattr(ai, "get_settings", lambda: SimpleNamespace(
        nvidia_api_key="test-key",
        tenderwise_reranker_model="nvidia/nemotron-3.5-rerank-8b",
        tenderwise_reranker_url="https://example.test/reranking",
    ))

    result = ai.rerank_passages("What is the EMD?", ["Passage A", "Passage B"])

    assert result == [(1, 0.9), (0, 0.2)]
    assert fake.last_request["url"] == "https://example.test/reranking"
    assert fake.last_request["json"]["query"] == {"text": "What is the EMD?"}
    assert fake.last_request["json"]["passages"] == [{"text": "Passage A"}, {"text": "Passage B"}]
    assert fake.last_request["headers"]["Authorization"] == "Bearer test-key"


def test_rerank_passages_rejects_invalid_and_duplicate_indices(monkeypatch):
    fake = _FakeClient()
    fake.body = {"rankings": [
        {"index": 1, "logit": 0.8},
        {"index": 1, "logit": 0.9},
        {"index": 99, "logit": 0.95},
        {"index": 0, "logit": float("nan")},
    ]}
    monkeypatch.setattr(ai.httpx, "Client", lambda *args, **kwargs: fake)
    monkeypatch.setattr(ai, "get_settings", lambda: SimpleNamespace(nvidia_api_key="test-key"))

    result = ai.rerank_passages("query", ["one", "two"])

    assert result == [(1, 0.8)]


def test_rerank_passages_requires_api_key(monkeypatch):
    monkeypatch.setattr(ai, "get_settings", lambda: SimpleNamespace(nvidia_api_key=None))
    with pytest.raises(ai.AIServiceError, match="not configured"):
        ai.rerank_passages("query", ["one"])
