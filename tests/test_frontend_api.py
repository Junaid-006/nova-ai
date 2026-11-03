import pytest
import httpx

from frontend.api import BackendError, NovaAPI


def test_protected_frontend_requests_send_bearer_token(monkeypatch):
    observed = {}

    def fake_request(method, url, **kwargs):
        observed.update(kwargs)
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(httpx, "request", fake_request)
    api = NovaAPI("http://127.0.0.1:8000")
    api.conversations("test-session-token")

    assert observed["headers"]["Authorization"] == "Bearer test-session-token"


def test_registration_does_not_send_an_auth_token(monkeypatch):
    observed = {}

    def fake_request(method, url, **kwargs):
        observed.update(kwargs)
        return httpx.Response(201, json={"access_token": "session"})

    monkeypatch.setattr(httpx, "request", fake_request)
    api = NovaAPI("http://127.0.0.1:8000")
    api.register("nova-user", "correct-horse-42")

    assert "Authorization" not in observed["headers"]


@pytest.mark.parametrize(
    "status_code, detail",
    [
        (502, "Nova AI is temporarily unable to respond."),
        (503, "The database is temporarily unavailable.")
    ]
)
def test_frontend_converts_backend_failures_to_clear_messages(
    monkeypatch, status_code, detail
):
    def fake_request(method, url, **kwargs):
        return httpx.Response(
            status_code,
            json={"detail": detail}
        )

    monkeypatch.setattr(httpx, "request", fake_request)
    api = NovaAPI("http://127.0.0.1:8000")

    with pytest.raises(BackendError) as error:
        api.send_message("hello", None, "test-session-token")

    assert str(error.value) == detail
    assert error.value.status_code == status_code
