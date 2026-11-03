from types import SimpleNamespace

import pytest

from backend import llm


class HttpFailure(Exception):
    def __init__(self, status_code, message="request failed"):
        super().__init__(message)
        self.status_code = status_code


class MockModels:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


class MockClient:
    def __init__(self, results):
        self.models = MockModels(results)


def test_fallback_failure_is_redacted_and_reported_generically(
    monkeypatch, capsys
):
    secret = "mock-gemini-secret"
    client = MockClient([
        HttpFailure(429, f"primary rejected key {secret}"),
        HttpFailure(503, f"fallback rejected key {secret}")
    ])
    monkeypatch.setattr(llm, "client", client)
    monkeypatch.setattr(llm, "API_KEY", secret)

    with pytest.raises(
        RuntimeError,
        match="Nova AI could not generate a response"
    ) as error:
        llm.generate_response("hello")

    output = capsys.readouterr().out
    assert str(error.value) == (
        "Nova AI could not generate a response. Please try again."
    )
    assert secret not in str(error.value)
    assert secret not in output
    assert len(client.models.calls) == 2


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(text=""),
        SimpleNamespace(),
        None
    ],
    ids=["empty-text", "missing-text", "missing-response"]
)
def test_empty_or_missing_response_is_a_safe_failure(
    result, monkeypatch, capsys
):
    monkeypatch.setattr(llm, "client", MockClient([result]))

    with pytest.raises(
        RuntimeError,
        match="Nova AI could not generate a response"
    ) as error:
        llm.generate_response("hello")

    assert str(error.value) == (
        "Nova AI could not generate a response. Please try again."
    )
    assert "Traceback" not in capsys.readouterr().out


@pytest.mark.parametrize("status_code", [429, 503])
def test_fallback_reuses_history_and_summary(status_code, monkeypatch):
    client = MockClient([
        HttpFailure(status_code),
        SimpleNamespace(text="answer")
    ])
    monkeypatch.setattr(llm, "client", client)
    monkeypatch.setattr(llm, "GENERATION_OPTIONS", {})
    history = [{"role": "user", "content": "earlier"}]

    answer = llm.generate_response("latest", history, "remembered fact")

    assert answer == "answer"
    primary, fallback = client.models.calls
    assert primary["model"] == "gemini-3.5-flash"
    assert fallback["model"] == "gemini-3.5-flash-lite"
    assert primary["contents"] is fallback["contents"]
    assert primary["contents"] == [
        {"role": "user", "parts": [{"text": "earlier"}]},
        {"role": "user", "parts": [{"text": "latest"}]}
    ]
    assert "remembered fact" in primary["config"].system_instruction
    assert primary["config"] is fallback["config"]


def test_empty_summary_uses_system_prompt_unchanged(monkeypatch):
    client = MockClient([SimpleNamespace(text="answer")])
    monkeypatch.setattr(llm, "client", client)
    monkeypatch.setattr(llm, "GENERATION_OPTIONS", {})

    assert llm.generate_response("hello", summary="") == "answer"
    assert client.models.calls[0]["config"].system_instruction == llm.SYSTEM_PROMPT


def test_non_retryable_error_does_not_fallback_or_expose_api_key(
    monkeypatch, capsys
):
    client = MockClient([HttpFailure(400, "test-api-key")])
    monkeypatch.setattr(llm, "client", client)
    monkeypatch.setattr(llm, "API_KEY", "test-api-key")

    with pytest.raises(RuntimeError):
        llm.generate_response("hello")

    assert len(client.models.calls) == 1
    assert "test-api-key" not in capsys.readouterr().out


def test_timeout_is_reported_without_retry(monkeypatch):
    client = MockClient([TimeoutError("request timed out")])
    monkeypatch.setattr(llm, "client", client)

    with pytest.raises(RuntimeError, match="could not generate a response"):
        llm.generate_response("hello")

    assert len(client.models.calls) == 1


def test_summary_uses_same_model_fallback(monkeypatch):
    client = MockClient([
        HttpFailure(503),
        SimpleNamespace(text="summary")
    ])
    monkeypatch.setattr(llm, "client", client)
    monkeypatch.setattr(llm, "GENERATION_OPTIONS", {})

    assert llm.generate_summary("summarize this") == "summary"
    assert [call["model"] for call in client.models.calls] == [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite"
    ]
