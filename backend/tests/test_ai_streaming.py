import json
from types import SimpleNamespace

import pytest

from app.services import ai


class FakeStreamResponse:
    status_code = 200

    def __init__(self, lines: list[str]):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_lines(self):
        return iter(self.lines)


class FakeClient:
    def __init__(self, response: FakeStreamResponse):
        self.response = response
        self.request = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, method, url, **kwargs):
        self.request = (method, url, kwargs)
        return self.response


def test_generate_response_stream_yields_deltas_and_sets_stream_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = [
        {"choices": [{"delta": {"role": "assistant"}}]},
        {"choices": [{"delta": {"content": "Tender deadline is "}}]},
        {"choices": [{"delta": {"content": "05 December 2026."}}]},
    ]
    response = FakeStreamResponse([f"data: {json.dumps(item)}" for item in payloads] + ["data: [DONE]"])
    client = FakeClient(response)
    settings = SimpleNamespace(
        nvidia_api_key="test-token",
        nvidia_model="test-model",
        nvidia_base_url="https://nim.example/v1",
    )
    monkeypatch.setattr(ai, "get_settings", lambda: settings)
    monkeypatch.setattr(ai.httpx, "Client", lambda **_kwargs: client)

    chunks = list(ai.generate_response_stream([{"role": "user", "content": "deadline?"}]))

    assert chunks == ["Tender deadline is ", "05 December 2026."]
    assert client.request is not None
    method, url, kwargs = client.request
    assert method == "POST"
    assert url == "https://nim.example/v1/chat/completions"
    assert kwargs["json"]["stream"] is True
    assert kwargs["headers"]["Accept"] == "text/event-stream"


def test_generate_response_stream_rejects_empty_provider_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient(FakeStreamResponse(["data: [DONE]"]))
    monkeypatch.setattr(ai, "get_settings", lambda: SimpleNamespace(
        nvidia_api_key="test-token",
        nvidia_model="test-model",
        nvidia_base_url="https://nim.example/v1",
    ))
    monkeypatch.setattr(ai.httpx, "Client", lambda **_kwargs: client)

    with pytest.raises(ai.AIServiceError, match="empty response"):
        list(ai.generate_response_stream([{"role": "user", "content": "hello"}]))
