"""HTTP client for the existing Nova AI FastAPI endpoints."""

from typing import Any

import httpx


class BackendError(RuntimeError):
    """A safe, user-facing error returned while calling the backend."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class NovaAPI:
    def __init__(self, base_url: str, timeout: float = 35.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        token: str | None = None,
        **kwargs: Any
    ) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout,
                headers=headers,
                **kwargs
            )
        except httpx.RequestError as error:
            raise BackendError(
                "Can't reach Nova AI. Check that the backend is running."
            ) from error

        if response.is_error:
            try:
                body = response.json()
            except ValueError:
                body = {}
            detail = body.get("detail")
            messages = {
                400: "That message is empty or invalid. Please try again.",
                404: "This conversation is no longer available.",
                502: "Nova AI couldn't generate a response just now. Please try again.",
                503: "The backend is temporarily unavailable."
            }
            message = detail if isinstance(detail, str) else messages.get(
                response.status_code,
                "The backend couldn't complete that request."
            )
            raise BackendError(message, response.status_code)

        if response.status_code == 204:
            return None

        try:
            return response.json()
        except ValueError as error:
            raise BackendError(
                "The backend returned an invalid response."
            ) from error

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def register(self, username: str, password: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/auth/register",
            json={"username": username, "password": password}
        )

    def login(self, username: str, password: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/auth/login",
            json={"username": username, "password": password}
        )

    def current_user(self, token: str) -> dict[str, Any]:
        return self._request("GET", "/auth/me", token=token)

    def logout(self, token: str) -> None:
        self._request("POST", "/auth/logout", token=token)

    def conversations(self, token: str) -> list[dict[str, Any]]:
        return self._request("GET", "/conversations", token=token)

    def conversation(
        self,
        conversation_id: str,
        token: str
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/conversations/{conversation_id}",
            token=token
        )

    def send_message(
        self,
        message: str,
        conversation_id: str | None,
        token: str
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/chat",
            token=token,
            json={
                "message": message,
                "conversation_id": conversation_id
            }
        )
