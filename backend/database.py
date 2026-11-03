
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

# Project database location
BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "nova_ai.db"


class ConversationNotFoundError(Exception):
    """Raised when a conversation is missing or belongs to another user."""


def get_connection():
    """Create and return a database connection."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def _managed_connection() -> Generator[sqlite3.Connection, None, None]:
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    """Create all required database tables."""
    with _managed_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id)
                    REFERENCES users (user_id) ON DELETE CASCADE
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT,
                FOREIGN KEY (user_id)
                    REFERENCES users (user_id) ON DELETE CASCADE
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations (conversation_id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                conversation_id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_summarized_message_id INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations (conversation_id)
            )
        """)

        summary_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(conversation_summaries)"
            )
        }
        if "last_summarized_message_id" not in summary_columns:
            connection.execute("""
                ALTER TABLE conversation_summaries
                ADD COLUMN last_summarized_message_id
                    INTEGER NOT NULL DEFAULT 0
            """)

        conversation_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(conversations)")
        }
        if "user_id" not in conversation_columns:
            connection.execute("""
                ALTER TABLE conversations
                ADD COLUMN user_id TEXT REFERENCES users(user_id)
                    ON DELETE CASCADE
            """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_user_created
            ON conversations(user_id, created_at DESC)
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiration
            ON auth_sessions(expires_at)
        """)


def create_conversation(user_id: str) -> str:
    """Create a conversation owned by a registered user."""
    conversation_id = str(uuid.uuid4())

    with _managed_connection() as connection:
        connection.execute(
            """
            INSERT INTO conversations (conversation_id, user_id)
            VALUES (?, ?)
            """,
            (conversation_id, user_id)
        )

    return conversation_id


def create_conversation_with_exchange(
    user_id: str,
    user_message: str,
    assistant_message: str
) -> str:
    """Atomically create an owned conversation and its first successful turn."""
    conversation_id = str(uuid.uuid4())
    with _managed_connection() as connection:
        connection.execute(
            """
            INSERT INTO conversations (conversation_id, user_id)
            VALUES (?, ?)
            """,
            (conversation_id, user_id)
        )
        connection.executemany(
            """
            INSERT INTO messages (conversation_id, role, content)
            VALUES (?, ?, ?)
            """,
            (
                (conversation_id, "user", user_message),
                (conversation_id, "assistant", assistant_message)
            )
        )
    return conversation_id


def save_message(conversation_id: str, role: str, content: str):
    """Save a message in a conversation."""
    with _managed_connection() as connection:
        connection.execute(
            """
            INSERT INTO messages (conversation_id, role, content)
            VALUES (?, ?, ?)
            """,
            (conversation_id, role, content)
        )


def save_exchange(
    conversation_id: str,
    user_id: str,
    user_message: str,
    assistant_message: str
) -> None:
    """Persist a turn only when the conversation belongs to the user."""
    with _managed_connection() as connection:
        owner = connection.execute(
            """
            SELECT 1 FROM conversations
            WHERE conversation_id = ? AND user_id = ?
            """,
            (conversation_id, user_id)
        ).fetchone()
        if owner is None:
            raise ConversationNotFoundError

        connection.executemany(
            """
            INSERT INTO messages (conversation_id, role, content)
            VALUES (?, ?, ?)
            """,
            (
                (conversation_id, "user", user_message),
                (conversation_id, "assistant", assistant_message)
            )
        )


def get_conversation_history(
    conversation_id: str,
    user_id: str
) -> list[dict[str, Any]]:
    """Retrieve a user's conversation messages in chronological order."""
    with _managed_connection() as connection:
        cursor = connection.execute(
            """
            SELECT m.message_id, m.role, m.content, m.created_at
            FROM messages AS m
            JOIN conversations AS c
                ON c.conversation_id = m.conversation_id
            WHERE m.conversation_id = ? AND c.user_id = ?
            ORDER BY m.message_id ASC
            """,
            (conversation_id, user_id)
        )
        messages = [dict(row) for row in cursor.fetchall()]

    return messages


def get_conversation(
    conversation_id: str,
    user_id: str
) -> dict[str, Any] | None:
    """Retrieve conversation metadata only for its owner."""
    with _managed_connection() as connection:
        row = connection.execute(
            """
            SELECT conversation_id, created_at
            FROM conversations
            WHERE conversation_id = ? AND user_id = ?
            """,
            (conversation_id, user_id)
        ).fetchone()
    return dict(row) if row else None


def list_conversations(user_id: str) -> list[dict[str, Any]]:
    """List only a user's conversations with message counts and previews."""
    with _managed_connection() as connection:
        rows = connection.execute(
            """
            SELECT c.conversation_id, c.created_at, COUNT(m.message_id)
                AS message_count,
                COALESCE((
                    SELECT content
                    FROM messages AS preview
                    WHERE preview.conversation_id = c.conversation_id
                        AND preview.role = 'user'
                    ORDER BY preview.message_id ASC
                    LIMIT 1
                ), '') AS preview
            FROM conversations AS c
            LEFT JOIN messages AS m
                ON m.conversation_id = c.conversation_id
            WHERE c.user_id = ?
            GROUP BY c.conversation_id, c.created_at
            ORDER BY c.created_at DESC, c.conversation_id
            """,
            (user_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def check_database() -> bool:
    """Check that SQLite is reachable."""
    with _managed_connection() as connection:
        connection.execute("SELECT 1").fetchone()
    return True


def compare_and_swap_summary(
    conversation_id: str,
    expected_summary: str,
    expected_checkpoint: int,
    summary: str,
    last_summarized_message_id: int
) -> bool:
    """Atomically update a summary only if its source state is unchanged."""
    if last_summarized_message_id <= expected_checkpoint:
        raise ValueError("A summary update must advance its checkpoint.")

    with _managed_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE conversation_summaries
            SET summary = ?,
                updated_at = CURRENT_TIMESTAMP,
                last_summarized_message_id = ?
            WHERE conversation_id = ?
                AND summary = ?
                AND conversation_summaries.last_summarized_message_id = ?
            """,
            (
                summary,
                last_summarized_message_id,
                conversation_id,
                expected_summary,
                expected_checkpoint
            )
        )
        if cursor.rowcount == 1:
            return True

        if expected_summary or expected_checkpoint != 0:
            return False

        cursor = connection.execute(
            """
            INSERT INTO conversation_summaries
                (conversation_id, summary, last_summarized_message_id)
            VALUES (?, ?, ?)
            ON CONFLICT(conversation_id) DO NOTHING
            """,
            (
                conversation_id,
                summary,
                last_summarized_message_id
            )
        )
        return cursor.rowcount == 1


def get_summary_state(conversation_id: str) -> tuple[str, int]:
    """Read summary text and checkpoint from the same database snapshot."""
    with _managed_connection() as connection:
        result = connection.execute(
            """
            SELECT summary, last_summarized_message_id
            FROM conversation_summaries
            WHERE conversation_id = ?
            """,
            (conversation_id,)
        ).fetchone()
    if result is None:
        return "", 0
    return result["summary"], int(result["last_summarized_message_id"])


def get_summary(conversation_id: str):
    """Retrieve a conversation summary."""
    return get_summary_state(conversation_id)[0]


def get_summary_checkpoint(conversation_id: str) -> int:
    """Retrieve the ID of the last message included in the summary."""
    return get_summary_state(conversation_id)[1]


def create_user(user_id: str, username: str, password_hash: str) -> None:
    """Create a user account."""
    with _managed_connection() as connection:
        connection.execute(
            """
            INSERT INTO users (user_id, username, password_hash)
            VALUES (?, ?, ?)
            """,
            (user_id, username, password_hash)
        )


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with _managed_connection() as connection:
        row = connection.execute(
            """
            SELECT user_id, username, password_hash
            FROM users WHERE username = ?
            """,
            (username,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    with _managed_connection() as connection:
        row = connection.execute(
            "SELECT user_id, username FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    return dict(row) if row else None


def create_auth_session(
    token_hash: str,
    user_id: str,
    expires_at: str
) -> None:
    with _managed_connection() as connection:
        connection.execute(
            """
            INSERT INTO auth_sessions (token_hash, user_id, expires_at)
            VALUES (?, ?, ?)
            """,
            (token_hash, user_id, expires_at)
        )


def get_auth_session(
    token_hash: str,
    now: str
) -> dict[str, Any] | None:
    with _managed_connection() as connection:
        row = connection.execute(
            """
            SELECT u.user_id, u.username
            FROM auth_sessions AS s
            JOIN users AS u ON u.user_id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ?
            """,
            (token_hash, now)
        ).fetchone()
    return dict(row) if row else None


def delete_auth_session(token_hash: str) -> None:
    with _managed_connection() as connection:
        connection.execute(
            "DELETE FROM auth_sessions WHERE token_hash = ?",
            (token_hash,)
        )


if __name__ == "__main__":
    initialize_database()
    print("Nova AI database initialized successfully!")

    print("\nAvailable database operations:")
    print("- create_conversation()")
    print("- save_message()")
    print("- get_conversation_history()")
    print("- list_conversations()")
    print("- compare_and_swap_summary()")
    print("- get_summary()")
