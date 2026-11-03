"""Password hashing and persistent bearer-session helpers."""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

import bcrypt

from backend import database

SESSION_LIFETIME = timedelta(days=7)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("ascii")
        )
    except (ValueError, TypeError):
        return False


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_session(user_id: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    expires_at = (
        datetime.now(timezone.utc) + SESSION_LIFETIME
    ).isoformat(timespec="seconds")
    database.create_auth_session(
        _token_digest(token),
        user_id,
        expires_at
    )
    return token, expires_at


def get_session_user(token: str) -> dict | None:
    return database.get_auth_session(
        _token_digest(token),
        datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


def revoke_session(token: str) -> None:
    database.delete_auth_session(_token_digest(token))
