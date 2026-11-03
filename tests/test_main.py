import pytest
from fastapi.testclient import TestClient

from backend import context_manager, database, llm
from backend import main


class MockGeminiFailure(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class MockGeminiModels:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


class MockGeminiClient:
    def __init__(self, results):
        self.models = MockGeminiModels(results)


def register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/auth/register",
        json={"username": username, "password": "correct-horse-42"}
    )
    assert response.status_code == 201
    return response.json()


def auth_headers(payload: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {payload['access_token']}"}


def test_health_and_owned_conversation_endpoints(temporary_database):
    with TestClient(main.app) as client:
        owner = register(client, "owner")
        user_id = owner["user"]["user_id"]
        conversation_id = database.create_conversation(user_id)
        database.save_message(conversation_id, "user", "hello")
        database.save_message(conversation_id, "assistant", "hi")
        headers = auth_headers(owner)

        assert client.get("/health").json() == {
            "status": "ok",
            "database": "ok"
        }
        conversations = client.get("/conversations", headers=headers)
        assert conversations.status_code == 200
        assert conversations.json()[0]["message_count"] == 2
        assert conversations.json()[0]["preview"] == "hello"

        detail = client.get(
            f"/conversations/{conversation_id}",
            headers=headers
        )
        assert detail.status_code == 200
        assert [message["content"] for message in detail.json()["messages"]] == [
            "hello", "hi"
        ]
        assert client.get(
            "/conversations/missing",
            headers=headers
        ).status_code == 404


def test_chat_saves_after_success_and_supports_short_history(
    temporary_database, monkeypatch
):
    with TestClient(main.app) as client:
        owner = register(client, "chat-owner")
        user_id = owner["user"]["user_id"]
        conversation_id = database.create_conversation(user_id)
        database.save_message(conversation_id, "user", "earlier")
        generated = []

        def generate_response(**kwargs):
            generated.append(kwargs)
            return "answer"

        monkeypatch.setattr(main, "generate_response", generate_response)
        response = client.post(
            "/chat",
            headers=auth_headers(owner),
            json={"message": "latest", "conversation_id": conversation_id}
        )

    assert response.status_code == 200
    assert generated[0]["history"][0]["content"] == "earlier"
    assert generated[0]["summary"] == ""
    assert [
        message["content"]
        for message in database.get_conversation_history(
            conversation_id,
            user_id
        )
    ] == ["earlier", "latest", "answer"]


def test_chat_creates_new_conversation_only_after_success(
    temporary_database, monkeypatch
):
    monkeypatch.setattr(main, "generate_response", lambda **_: "new answer")

    with TestClient(main.app) as client:
        owner = register(client, "new-owner")
        headers = auth_headers(owner)
        response = client.post(
            "/chat",
            headers=headers,
            json={"message": "start", "conversation_id": None}
        )

    assert response.status_code == 200
    conversation_id = response.json()["conversation_id"]
    user_id = owner["user"]["user_id"]
    assert database.get_conversation(conversation_id, user_id)
    assert [
        message["content"]
        for message in database.get_conversation_history(
            conversation_id,
            user_id
        )
    ] == ["start", "new answer"]


def test_chat_validation_unknown_id_and_llm_failure(
    temporary_database, monkeypatch
):
    with TestClient(main.app) as client:
        owner = register(client, "error-owner")
        user_id = owner["user"]["user_id"]
        conversation_id = database.create_conversation(user_id)
        monkeypatch.setattr(
            main,
            "generate_response",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("unavailable"))
        )

        assert client.post(
            "/chat",
            headers=auth_headers(owner),
            json={"message": " "}
        ).status_code == 400
        assert client.post(
            "/chat",
            headers=auth_headers(owner),
            json={"message": "hello", "conversation_id": "missing"}
        ).status_code == 404
        response = client.post(
            "/chat",
            headers=auth_headers(owner),
            json={"message": "hello", "conversation_id": conversation_id}
        )

    assert response.status_code == 502
    assert database.get_conversation_history(conversation_id, user_id) == []


@pytest.mark.parametrize(
    "results",
    [
        [
            MockGeminiFailure(429, "rate limited"),
            MockGeminiFailure(503, "fallback unavailable")
        ],
        [
            MockGeminiFailure(503, "service unavailable"),
            MockGeminiFailure(429, "fallback rate limited")
        ],
        [MockGeminiFailure(504, "upstream timeout")],
        [MockGeminiFailure(400, "invalid key: mock-sensitive-key")],
        [type("EmptyResponse", (), {"text": ""})()],
        [None]
    ],
    ids=[
        "429-and-fallback-503",
        "503-and-fallback-429",
        "504-timeout",
        "400-invalid-key",
        "empty-response",
        "missing-response"
    ]
)
def test_gemini_failures_return_safe_502_without_creating_exchange(
    temporary_database, monkeypatch, capsys, results
):
    gemini = MockGeminiClient(results)
    monkeypatch.setattr(llm, "client", gemini)
    monkeypatch.setattr(llm, "API_KEY", "mock-sensitive-key")

    with TestClient(main.app) as client:
        owner = register(client, "gemini-failure-user")
        response = client.post(
            "/chat",
            headers=auth_headers(owner),
            json={"message": "this should not be saved"}
        )
        health = client.get("/health")

    assert response.status_code == 502
    assert health.status_code == 200
    assert response.json() == {
        "detail": "Nova AI is temporarily unable to respond."
    }
    assert "mock-sensitive-key" not in response.text
    assert "mock-sensitive-key" not in capsys.readouterr().out
    assert database.list_conversations(owner["user"]["user_id"]) == []
    assert len(gemini.models.calls) == len(results)


def test_gemini_failure_does_not_append_message_to_existing_conversation(
    temporary_database, monkeypatch
):
    gemini = MockGeminiClient([
        MockGeminiFailure(504, "upstream timeout")
    ])
    monkeypatch.setattr(llm, "client", gemini)

    with TestClient(main.app) as client:
        owner = register(client, "existing-gemini-failure-user")
        user_id = owner["user"]["user_id"]
        conversation_id = database.create_conversation(user_id)
        database.save_exchange(
            conversation_id,
            user_id,
            "saved before failure",
            "existing answer"
        )
        response = client.post(
            "/chat",
            headers=auth_headers(owner),
            json={
                "message": "this failed turn must not persist",
                "conversation_id": conversation_id
            }
        )

    assert response.status_code == 502
    assert [
        message["content"]
        for message in database.get_conversation_history(
            conversation_id,
            user_id
        )
    ] == ["saved before failure", "existing answer"]


def test_endpoint_uses_persistent_summary_without_resummarizing(
    temporary_database, monkeypatch
):
    with TestClient(main.app) as client:
        owner = register(client, "summary-owner")
        user_id = owner["user"]["user_id"]
        conversation_id = database.create_conversation(user_id)
        for index in range(12):
            database.save_message(
                conversation_id,
                "user",
                f"message-{index}"
            )
        calls = []
        monkeypatch.setattr(
            context_manager,
            "generate_summary",
            lambda prompt: calls.append(prompt) or (
                "memory message-0 message-1 message-2 message-3 "
                "message-4 message-5"
            )
        )
        monkeypatch.setattr(main, "generate_response", lambda **_: "answer")
        headers = auth_headers(owner)
        first = client.post(
            "/chat",
            headers=headers,
            json={"message": "first", "conversation_id": conversation_id}
        )
        second = client.post(
            "/chat",
            headers=headers,
            json={"message": "second", "conversation_id": conversation_id}
        )

    assert first.status_code == second.status_code == 200
    assert len(calls) == 2
    assert "user: message-0" not in calls[1]
    assert database.get_summary_checkpoint(conversation_id) > 0


def test_chat_uses_configured_recent_message_limit(
    temporary_database, monkeypatch
):
    with TestClient(main.app) as client:
        owner = register(client, "configured-limit-owner")
        conversation_id = database.create_conversation(
            owner["user"]["user_id"]
        )
        for index in range(7):
            database.save_message(
                conversation_id,
                "user",
                f"message {index}"
            )
        generated = []
        monkeypatch.setattr(main, "MAX_RECENT_MESSAGES", 3)
        monkeypatch.setattr(
            context_manager,
            "generate_summary",
            lambda _prompt: "message 0 1 2 3 details"
        )
        monkeypatch.setattr(
            main,
            "generate_response",
            lambda **kwargs: generated.append(kwargs) or "answer"
        )

        response = client.post(
            "/chat",
            headers=auth_headers(owner),
            json={"message": "latest", "conversation_id": conversation_id}
        )

    assert response.status_code == 200
    assert [message["content"] for message in generated[0]["history"]] == [
        "message 4",
        "message 5",
        "message 6"
    ]


def test_two_users_have_isolated_conversations_and_idor_protection(
    temporary_database, monkeypatch
):
    monkeypatch.setattr(main, "generate_response", lambda **_: "private answer")

    with TestClient(main.app) as client:
        user_a = register(client, "user-a")
        user_b = register(client, "user-b")
        headers_a = auth_headers(user_a)
        headers_b = auth_headers(user_b)

        first = client.post(
            "/chat",
            headers=headers_a,
            json={"message": "A private topic"}
        )
        second = client.post(
            "/chat",
            headers=headers_a,
            json={"message": "Another private topic"}
        )
        conversation_a = first.json()["conversation_id"]
        conversation_a2 = second.json()["conversation_id"]

        assert first.status_code == second.status_code == 200
        a_list = client.get("/conversations", headers=headers_a).json()
        b_list = client.get("/conversations", headers=headers_b).json()
        assert {item["conversation_id"] for item in a_list} == {
            conversation_a, conversation_a2
        }
        assert all(
            "A private topic" not in item["preview"]
            for item in b_list
        )
        assert b_list == []

        assert client.get(
            f"/conversations/{conversation_a}",
            headers=headers_b
        ).status_code == 404
        denied_post = client.post(
            "/chat",
            headers=headers_b,
            json={
                "message": "attempted access",
                "conversation_id": conversation_a
            }
        )
        assert denied_post.status_code == 404

        a_detail = client.get(
            f"/conversations/{conversation_a}",
            headers=headers_a
        ).json()
        assert [message["content"] for message in a_detail["messages"]] == [
            "A private topic", "private answer"
        ]


def test_auth_validation_login_logout_and_protected_endpoints(
    temporary_database
):
    with TestClient(main.app) as client:
        assert client.get("/conversations").status_code == 401
        assert client.get("/conversations/secret-id").status_code == 401
        assert client.post("/chat", json={"message": "unauthorized"}).status_code == 401

        invalid_registration = client.post(
            "/auth/register",
            json={"username": "x", "password": "short"}
        )
        assert invalid_registration.status_code == 400

        registered = register(client, "login-user")
        assert client.post(
            "/auth/register",
            json={"username": "LOGIN-USER", "password": "correct-horse-42"}
        ).status_code == 409

        failed_login = client.post(
            "/auth/login",
            json={"username": "login-user", "password": "wrong-password"}
        )
        assert failed_login.status_code == 401

        login = client.post(
            "/auth/login",
            json={"username": "LOGIN-USER", "password": "correct-horse-42"}
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        assert client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        ).status_code == 200
        assert client.post(
            "/auth/logout",
            headers=auth_headers(registered)
        ).status_code == 204
        assert client.get(
            "/auth/me",
            headers=auth_headers(registered)
        ).status_code == 401
