import sqlite3

import pytest

from backend import database


def test_database_persists_conversations_messages_and_summaries(
    temporary_database
):
    user_id = "user-a"
    database.create_user(user_id, "user-a", "hashed-password")
    conversation_id = database.create_conversation(user_id)
    database.save_exchange(conversation_id, user_id, "hello", "hi")

    history = database.get_conversation_history(conversation_id, user_id)
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert [message["content"] for message in history] == ["hello", "hi"]
    assert all(message["message_id"] for message in history)

    assert database.compare_and_swap_summary(
        conversation_id,
        "",
        0,
        "User said hello.",
        history[0]["message_id"]
    )
    assert database.get_summary(conversation_id) == "User said hello."
    assert database.get_summary_checkpoint(conversation_id) == history[0]["message_id"]
    assert database.get_conversation(
        conversation_id,
        user_id
    )["conversation_id"] == conversation_id
    assert database.list_conversations(user_id)[0]["message_count"] == 2
    assert database.list_conversations("other-user") == []
    assert database.check_database()


def test_database_upgrade_preserves_existing_records(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE conversation_summaries (
                conversation_id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO conversations (conversation_id) VALUES ('legacy');
            INSERT INTO messages (conversation_id, role, content)
                VALUES ('legacy', 'user', 'preserve me');
            INSERT INTO conversation_summaries (conversation_id, summary)
                VALUES ('legacy', 'old summary');
        """)

    monkeypatch.setattr(database, "DATABASE_PATH", path)
    database.initialize_database()

    assert database.get_conversation_history("legacy", "new-user") == []
    assert database.get_summary("legacy") == "old summary"
    assert database.get_summary_checkpoint("legacy") == 0
    assert database.get_summary_state("legacy") == ("old summary", 0)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT user_id FROM conversations WHERE conversation_id = 'legacy'"
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT content FROM messages WHERE conversation_id = 'legacy'"
        ).fetchone()[0] == "preserve me"


def test_summary_compare_and_swap_requires_matching_state(
    temporary_database
):
    user_id = "cas-owner"
    database.create_user(user_id, user_id, "hashed-password")
    conversation_id = database.create_conversation(user_id)
    assert database.compare_and_swap_summary(
        conversation_id,
        "",
        0,
        "current summary",
        5
    )

    assert not database.compare_and_swap_summary(
        conversation_id,
        "stale summary",
        3,
        "stale replacement",
        10
    )
    assert database.get_summary_state(conversation_id) == (
        "current summary",
        5
    )
    with pytest.raises(ValueError, match="advance"):
        database.compare_and_swap_summary(
            conversation_id,
            "current summary",
            5,
            "not advanced",
            5
        )


def test_save_exchange_rolls_back_if_conversation_does_not_exist(
    temporary_database
):
    with pytest.raises(database.ConversationNotFoundError):
        database.save_exchange("missing", "owner", "user", "assistant")

    assert database.get_conversation_history("missing", "owner") == []
